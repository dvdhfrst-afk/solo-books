"""JSON for other programs. The shape is described in docs/EXPORTS.md and changes only with format_version.

Every amount is an object holding whole cents and the same amount as a decimal string, so a reader never needs a
float. Figures read the way the reports read: income and expenses positive, returns negative under income.
"""
from __future__ import annotations

import json
from typing import Callable, Dict, List

from .. import payees
from ..analysis import percent_change
from ..budgets import percent_used
from ..reports import NOT_ADVICE, PnlRow
from ..render import _PART_TITLES, _schedule_notes
from .base import GENERATOR, Format, Report, money, period_json

FORMAT_VERSION = 1


def _category(entry: PnlRow, section: str, negate: bool = False) -> dict:
    values = [-v for v in entry.values] if negate else entry.values
    return {
        "section": section,
        "code": entry.code,
        "name": entry.name,
        "kind": entry.kind,
        "values": [money(v) for v in values],
        "total": money(sum(values)),
    }


def _series(values: List[int]) -> dict:
    return {"values": [money(v) for v in values], "total": money(sum(values))}


def _pnl_categories(pnl) -> List[dict]:
    return (
        [_category(e, "income") for e in pnl.income]
        + [_category(e, "returns", negate=True) for e in pnl.returns]
        + [_category(e, "cost of goods sold") for e in pnl.cogs]
        + [_category(e, "expense") for e in pnl.expenses]
    )


def _pnl_totals(pnl) -> dict:
    return {
        "total_income": _series(pnl.total_income),
        "total_cogs": _series(pnl.total_cogs),
        "gross_profit": _series(pnl.gross_profit),
        "total_expenses": _series(pnl.total_expenses),
        "net": _series(pnl.net),
    }


def _uncategorized(count: int, money_in: int, money_out: int) -> dict:
    return {"count": count, "money_in": money(money_in), "money_out": money(money_out)}


def _pnl(report: Report) -> dict:
    businesses = []
    for pnl in report.parts:
        businesses.append({
            "business": pnl.business,
            "period": period_json(pnl.period),
            "buckets": [period_json(b) for b in pnl.buckets],
            "categories": _pnl_categories(pnl),
            "totals": _pnl_totals(pnl),
            "not_income_or_expense": [_category(e, "not income or expense") for e in pnl.off.excluded],
            "uncategorized": _uncategorized(pnl.off.uncategorized_count, pnl.off.uncategorized_in, pnl.off.uncategorized_out),
        })
    result = {"period": period_json(report.period), "tag": report.tag, "businesses": businesses}
    if len(report.parts) > 1:
        result["all_businesses"] = {
            name: money(sum(sum(getattr(p, name)) for p in report.parts))
            for name in ("total_income", "total_cogs", "gross_profit", "total_expenses", "net")
        }
    return result


def _balances(report: Report) -> dict:
    businesses = []
    for part in report.parts:
        businesses.append({
            "business": part.business,
            "period": period_json(part.period),
            "accounts": [
                {
                    "name": a.name,
                    "kind": a.kind,
                    "shows": "owed" if a.kind == "credit_card" else "held",
                    "start": money(a.printed(a.begin)),
                    "opened_in_period": money(a.printed(a.opened)),
                    "end": money(a.printed(a.end)),
                }
                for a in part.accounts
            ],
            "money_at_start": money(part.money_begin),
            "accounts_opened": money(part.money_opened),
            "net_profit": money(part.profit),
            "not_profit": [
                {"code": row.code, "name": row.name, "kind": row.kind, "net": money(row.total)} for row in part.off_profit
            ],
            "owner_money": money(part.owner_net),
            "transfers": money(part.transfers_net),
            "waiting_for_review": {"count": part.waiting_count, "net": money(part.waiting_net)},
            "money_at_end": money(part.money_end),
            "inventory_end": None if part.inventory_end is None else money(part.inventory_end),
            "warnings": list(part.warnings),
        })
    result = {"period": period_json(report.period), "businesses": businesses}
    if len(report.parts) > 1:
        parts = report.parts
        result["all_businesses"] = {
            "money_at_start": money(sum(p.money_begin + p.money_opened for p in parts)),
            "net_profit": money(sum(p.profit for p in parts)),
            "owner_money": money(sum(p.owner_net for p in parts)),
            "transfers": money(sum(p.transfers_net for p in parts)),
            "waiting_for_review": money(sum(p.waiting_net for p in parts)),
            "money_at_end": money(sum(p.money_end for p in parts)),
        }
    return result


def _schedule_c(report: Report) -> dict:
    businesses = []
    for part in report.parts:
        form = part.form
        businesses.append({
            "business": part.business,
            "tax_year": part.tax_year,
            "form": {
                "form": form.form,
                "tax_year": form.tax_year,
                "revision": form.revision,
                "source_url": form.source_url,
                "retrieved": form.retrieved,
            },
            "lines": [
                {
                    "part": entry.entry["part"],
                    "part_title": _PART_TITLES.get(entry.entry["part"], f"Part {entry.entry['part']}"),
                    "line": entry.line,
                    "key": entry.entry.get("key"),
                    "label": entry.label,
                    "amount": money(entry.cents),
                    "computed": entry.computed,
                    "categories": [{"code": c.code, "name": c.name, "amount": money(c.total)} for c in entry.categories],
                    "note": entry.entry.get("note", "") if entry.cents != 0 else "",
                }
                for entry in part.lines
            ],
            "ties_to_profit_and_loss": {
                "net_profit": money(part.pnl_net),
                "inventory_change": money(part.inventory_change),
                "left_off_the_form": money(part.off_form_net),
                "line_31": money(part.net_line.cents),
            },
            "left_off_the_form": [{"code": r.code, "name": r.name, "amount": money(r.total)} for r in part.off_form],
            "inventory_entered": part.inventory_entered,
            "uncategorized": _uncategorized(part.uncategorized_count, part.uncategorized_in, part.uncategorized_out),
            "warnings": list(part.warnings),
            "notes": _schedule_notes(part),
        })
    return {"tax_year": report.parts[0].tax_year, "businesses": businesses}


def _flow(line, key: str = "label") -> dict:
    return {
        key: line.label,
        "rows": line.rows,
        "buckets": [
            {"money_in": money(i), "money_out": money(o), "net": money(i - o)} for i, o in zip(line.money_in, line.money_out)
        ],
        "money_in": money(line.total_in),
        "money_out": money(line.total_out),
        "net": money(line.total_net),
    }


def _cashflow(report: Report) -> dict:
    businesses = []
    for part in report.parts:
        accounts = []
        for line in part.accounts:
            entry = _flow(line, "name")
            entry["kind"] = line.account_kind
            accounts.append(entry)
        businesses.append({
            "business": part.business,
            "period": period_json(part.period),
            "buckets": [period_json(b) for b in part.buckets],
            "accounts": accounts,
            "all_accounts": _flow(part.total),
            "what_moved_it": [_flow(line) for line in part.purposes],
            "waiting_for_review": _flow(part.waiting) if part.waiting else None,
            "warnings": list(part.warnings),
        })
    result = {"period": period_json(report.period), "tag": report.tag, "businesses": businesses}
    if len(report.parts) > 1:
        totals = [p.total for p in report.parts]
        result["all_businesses"] = {
            "money_in": money(sum(t.total_in for t in totals)),
            "money_out": money(sum(t.total_out for t in totals)),
            "net": money(sum(t.total_net for t in totals)),
        }
    return result


def _trend(report: Report) -> dict:
    businesses = []
    for part in report.parts:
        pnl = part.pnl

        def row(entry: dict, values: List[int]) -> dict:
            entry["change"] = money(values[-1] - values[0])
            entry["change_percent"] = percent_change(values[0], values[-1])
            return entry

        categories = []
        for entry in pnl.income + pnl.returns + pnl.cogs + pnl.expenses + pnl.off.excluded:
            negate = entry.kind == "returns"
            section = {
                "income": "income", "returns": "returns", "cogs": "cost of goods sold", "expense": "expense",
            }.get(entry.kind, "not income or expense")
            shown = _category(entry, section, negate)
            values = [-v for v in entry.values] if negate else entry.values
            shown.pop("total")
            categories.append(row(shown, values))
        totals = {}
        for name, series in _pnl_totals(pnl).items():
            values = getattr(pnl, name)
            totals[name] = row({"values": series["values"]}, values)
        businesses.append({
            "business": part.business,
            "years": part.years,
            "categories": categories,
            "totals": totals,
            "uncategorized_rows": dict(zip((str(y) for y in part.years), part.waiting)),
        })
    return {"years": report.parts[0].years, "tag": report.tag, "businesses": businesses}


def _stat(stat) -> dict:
    return {
        "code": stat.code or None,
        "name": stat.name,
        "kind": stat.kind,
        "rows": stat.rows,
        "total": money(stat.total),
        "average": money(stat.average),
        "largest": {
            "transaction_id": stat.largest_id,
            "date": stat.largest_on,
            "amount": money(stat.largest_amount),
            "description": stat.largest_description,
        },
    }


def _categories(report: Report) -> dict:
    businesses = [
        {
            "business": part.business,
            "period": period_json(part.period),
            "categories": [_stat(s) for s in part.categories],
            "waiting_for_review": _stat(part.waiting) if part.waiting else None,
        }
        for part in report.parts
    ]
    return {"period": period_json(report.period), "tag": report.tag, "businesses": businesses}


def _budget_line(code: str, name: str, budget: int, actual: int) -> dict:
    used = percent_used(actual, budget)
    return {
        "code": code or None,
        "name": name,
        "budget": money(budget),
        "actual": money(actual),
        "difference": money(actual - budget),
        "percent_used": used.rstrip("%") if used else None,
    }


def _budget(report: Report) -> dict:
    businesses = []
    for part in report.parts:
        sections = []
        for section, _, lines, total_name, total in part.sections():
            sections.append({
                "section": section,
                "categories": [_budget_line(line.code, line.name, line.budget, line.actual) for line in lines],
                "total": _budget_line("", total_name, *total),
            })
        off = part.off
        businesses.append({
            "business": part.business,
            "period": period_json(part.period),
            "sections": sections,
            "net": _budget_line("", "Net profit or (loss)", *part.net),
            "uncategorized": _uncategorized(off.uncategorized_count, off.uncategorized_in, off.uncategorized_out),
            "warnings": list(part.warnings),
        })
    return {"period": period_json(report.period), "tag": report.tag, "businesses": businesses}


def _payee_total(entry: payees.PayeeTotal, rank: int = 0) -> dict:
    shown = {"rank": rank} if rank else {}
    shown.update({
        "payee": entry.payee.name,
        "spellings": list(entry.payee.merchants),
        "contractor": entry.payee.contractor,
        "rows": entry.rows,
        "money_out": money(entry.money_out),
        "money_in": money(entry.money_in),
        "net_paid": money(entry.net_paid),
        "paid_by_card": money(entry.by_card),
    })
    return shown


def _payees(report: Report) -> dict:
    businesses = [
        {
            "business": part.business,
            "period": period_json(part.period),
            "money_out": [_payee_total(e, rank) for rank, e in enumerate(payees.top(part, "out", report.limit), start=1)],
            "money_in": [_payee_total(e, rank) for rank, e in enumerate(payees.top(part, "in", report.limit), start=1)],
            "transfer_and_owner_rows_left_out": part.left_out_count,
            "uncategorized": _uncategorized(part.waiting_count, part.waiting_in, part.waiting_out),
        }
        for part in report.parts
    ]
    return {"period": period_json(report.period), "tag": report.tag, "limit": report.limit, "businesses": businesses}


def _contractors(report: Report) -> dict:
    businesses = [
        {
            "business": part.business,
            "threshold": money(part.threshold),
            "flagged": part.flagged,
            "at_or_over": [_payee_total(e) for e in part.at_or_over],
            "under": [_payee_total(e) for e in part.under],
            "waiting_for_review": {"count": part.waiting_count, "money_out": money(part.waiting_out)},
        }
        for part in report.parts
    ]
    return {
        "tax_year": report.period.start.year, "notice": payees.CONTRACTORS_NOTICE, "businesses": businesses,
    }


_BODIES: Dict[str, Callable[[Report], dict]] = {
    "pnl": _pnl,
    "balances": _balances,
    "schedule-c": _schedule_c,
    "cashflow": _cashflow,
    "trend": _trend,
    "categories": _categories,
    "budget": _budget,
    "payees": _payees,
    "contractors": _contractors,
}


def payload(report: Report) -> dict:
    return {"format_version": FORMAT_VERSION, "report": report.kind, "generator": GENERATOR, "note": NOT_ADVICE, **_BODIES[report.kind](report)}


def dumps(value: dict) -> str:
    return json.dumps(value, indent=2, ensure_ascii=False) + "\n"


def bundle(year: int, reports: List[Report]) -> str:
    """Several reports in one document, as the preparer folder's summary.json holds them."""
    return dumps({
        "format_version": FORMAT_VERSION,
        "report": "preparer-summary",
        "generator": GENERATOR,
        "note": NOT_ADVICE,
        "tax_year": year,
        "reports": [payload(report) for report in reports],
    })


class JsonFormat(Format):
    name = "json"
    help = "JSON for other programs, amounts in whole cents (see docs/EXPORTS.md)"

    def render(self, report: Report) -> str:
        return dumps(payload(report))


FORMAT = JsonFormat()
