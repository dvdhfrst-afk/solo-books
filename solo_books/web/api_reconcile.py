"""Reconciling through the API: running a statement period, finishing it, reopening it, and listing them."""
from __future__ import annotations

from .. import reconcile
from ..money import format_cents
from .api import Request, account_by_id, money, printed_money, refusals_as, transaction_json
from .routes import api


def _report_json(report: reconcile.ReconcileReport) -> dict:
    recon, account = report.reconciliation, report.account
    data = {
        "id": recon["id"],
        "account_id": account["id"],
        "account": account["name"],
        "printed_as_owed": account["kind"] == "credit_card",
        "from": report.start.isoformat(),
        "to": report.end.isoformat(),
        "status": recon["status"],
        "finished_at": recon["finished_at"],
        "rows": report.row_count,
        "balances_tie": report.balances_tie,
        "balanced": report.balanced,
        "difference_says": None,
        "prior_id": report.prior_id,
        "hints": report.hints,
        "notes": report.notes,
        "rows_added_after_finish": [transaction_json(row) for row in report.rows_added_after_finish],
    }
    if report.difference:
        more = "more money in (or less out)" if report.difference > 0 else "more money out (or less in)"
        data["difference_says"] = (
            f"Difference: {format_cents(abs(report.difference))}. The statement shows {more} than the books."
        )
    data.update(printed_money("statement_begin", account, report.statement_begin))
    data.update(printed_money("statement_end", account, report.statement_end))
    data.update(printed_money("books_end", account, report.expected_end))
    data.update(printed_money("books_begin", account, report.book_begin))
    data.update(money("money_in", report.money_in))
    data.update(money("money_out", report.money_out))
    data.update(money("difference", report.difference))
    data.update(money("begin_gap", report.begin_gap))
    data.update(money("prior_gap", report.prior_gap))
    return data


def _recon(conn, recon_id: int):
    with refusals_as(404):
        return reconcile.get(conn, recon_id)


@api("GET", "reconciliations")
def reconciliation_list(conn, request: Request) -> dict:
    query = request.params_named("account")
    account_id = query.number("account")
    if account_id is not None:
        account_by_id(conn, account_id)
    found = []
    for recon in reconcile.list_for_account(conn, account_id):
        report = reconcile.compute(conn, recon)
        entry = {
            "id": recon["id"],
            "account_id": recon["account_id"],
            "account": recon["account_name"],
            "from": recon["period_start"],
            "to": recon["period_end"],
            "status": recon["status"],
            "balanced": report.balances_tie,
        }
        entry.update(printed_money("statement_begin", report.account, recon["statement_begin_cents"]))
        entry.update(printed_money("statement_end", report.account, recon["statement_end_cents"]))
        entry.update(money("difference", report.difference))
        found.append(entry)
    return {"reconciliations": found}


@api("GET", "reconciliations/{id}")
def reconciliation_detail(conn, request: Request) -> dict:
    request.params_named()
    return {"reconciliation": _report_json(reconcile.compute(conn, _recon(conn, request.params["id"])))}


@api("POST", "reconciliations")
def reconciliation_run(conn, request: Request) -> dict:
    """Record a statement's figures for a period, as printed (on a card, what is owed), and compare the books.

    Running the same period again replaces the figures of an open reconciliation.
    """
    fields = request.fields("account_id", "from", "to", "begin", "end")
    account = account_by_id(conn, fields.identifier("account_id", required=True))
    start, end = fields.day("from"), fields.day("to")
    begin, ending = fields.amount("begin", required=True), fields.amount("end", required=True)
    # A period that runs backward is a mistake in the request; one that overlaps a finished period is the books' state.
    with refusals_as(422 if end < start else 409):
        report = reconcile.run(conn, account, start, end, begin, ending)
    return {"reconciliation": _report_json(report)}


@api("POST", "reconciliations/{id}/finish")
def reconciliation_finish(conn, request: Request) -> dict:
    """Lock a period that ties to the cent. Its rows cannot be deleted or edited until it is reopened."""
    request.fields()
    recon = _recon(conn, request.params["id"])
    with refusals_as(409):
        report = reconcile.finish(conn, recon["id"])
    return {"reconciliation": _report_json(report)}


@api("POST", "reconciliations/{id}/reopen")
def reconciliation_reopen(conn, request: Request) -> dict:
    """Unlock the latest finished period of an account."""
    request.fields()
    recon = _recon(conn, request.params["id"])
    with refusals_as(409):
        reconcile.reopen(conn, recon["id"])
    return {"reconciliation": _report_json(reconcile.compute(conn, reconcile.get(conn, recon["id"])))}
