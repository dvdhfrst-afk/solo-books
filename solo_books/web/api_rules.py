"""Categorization rules through the API: listing, adding, enabling and disabling, moving, deleting, testing a
description, explaining a row, and running them."""
from __future__ import annotations

from .. import books, ledger, rule_packs, rules
from ..conditions import describe_all
from ..text import merchant_key
from .api import ApiError, Request, account_by_id, business_by_id, category_json, chosen_business, money, refusals_as
from .routes import api

# 'rules test' without an amount tries a dollar of money out.
_TEST_AMOUNT_CENTS = -100


def _rule_json(conn, rule: rules.Rule, listed, business, place: int, names) -> dict:
    """A rule with every condition, and the older single fields for scripts that read a rule that way."""
    data = {
        "id": rule.id,
        "business_id": business["id"],
        "business": business["name"],
        "place": place,
        "priority": rule.priority,
        "enabled": rule.enabled,
        "conditions": rule_packs.conditions_to_json(rule, names),
        "said": rules.said(conn, rule),
        "conditions_said": describe_all(rule.conditions, names),
        "category": {"id": rule.category_id, "code": listed["category_code"], "name": listed["category_name"]},
        "note": rule.note,
        "match": listed["match_type"],
        "pattern": listed["pattern"],
        "direction": listed["direction"],
        "account_id": listed["account_id"],
        "account": listed["account_name"],
        "rows": listed["hits"],
    }
    data.update(money("min", listed["min_cents"]))
    data.update(money("max", listed["max_cents"]))
    return data


def rules_of(conn, business) -> list:
    names = rules.account_names(conn)
    listed = {row["id"]: row for row in rules.list_rules(conn, business["id"])}
    return [
        _rule_json(conn, rule, listed[rule.id], business, place, names)
        for place, rule in enumerate(rules.load_rules(conn, business["id"], include_disabled=True), start=1)
    ]


def _one_rule(conn, rule_id: int) -> dict:
    with refusals_as(404):
        rule = rules.get_rule(conn, rule_id)
    business = business_by_id(conn, rule.business_id or 0)
    return [entry for entry in rules_of(conn, business) if entry["id"] == rule_id][0]


@api("GET", "rules")
def rule_list(conn, request: Request) -> dict:
    """In the order they are tried, disabled ones included. Without business=ID, every business's, business by business."""
    query = request.params_named("business")
    business_id = query.number("business")
    chosen = [business_by_id(conn, business_id)] if business_id is not None else books.list_businesses(conn)
    return {"rules": [entry for business in chosen for entry in rules_of(conn, business)]}


def _account_in(conn, business, account_id):
    if account_id is None:
        return None
    account = account_by_id(conn, account_id)
    if account["business_id"] != business["id"]:
        raise ApiError(422, f"account {account['name']} belongs to another business")
    return account


_OLD_FIELDS = ("match", "pattern", "direction", "min", "max", "account_id")


@api("POST", "rules")
def rule_add(conn, request: Request):
    """Add a rule and run the rules over the queue, as 'rules add' does.

    The conditions come as a list, written the way a rule pack writes them. The older single fields, match and
    pattern with direction, min, max and account_id, still work, and the two can be combined.
    """
    fields = request.fields("business_id", "conditions", "category", "priority", "note", "enabled", *_OLD_FIELDS)
    business = chosen_business(conn, fields.identifier("business_id"))
    category = books.find_category(conn, business["id"], fields.text("category", required=True) or "")
    raw = request.body.get("conditions")
    conditions = rule_packs.conditions_from_json(conn, business["id"], raw) if raw is not None else []
    match = fields.text("match")
    if match is None and fields.text("pattern") is not None:
        raise ApiError(400, "pattern needs match")
    account = _account_in(conn, business, fields.identifier("account_id"))
    if match is None and not conditions and all(request.body.get(name) is None for name in _OLD_FIELDS):
        raise ApiError(400, "conditions is required: a list such as [{\"kind\": \"contains\", \"text\": \"CARD PAYMENT\"}]")
    enabled = fields.flag("enabled") if "enabled" in request.body else True
    rule_id = rules.add_rule(
        conn,
        business["id"],
        match,
        fields.text("pattern", required=match is not None),
        category,
        priority=fields.number("priority", 100),
        account_id=account["id"] if account else None,
        direction=fields.text("direction"),
        min_cents=fields.amount("min"),
        max_cents=fields.amount("max"),
        note=fields.text("note") or "",
        conditions=conditions,
    )
    if not enabled:
        rules.set_enabled(conn, rule_id, False)
    outcome = rules.apply_rules(conn, business["id"])
    return 201, {"rule": _one_rule(conn, rule_id), "categorized": outcome["categorized"]}


@api("DELETE", "rules/{id}")
def rule_delete(conn, request: Request) -> dict:
    """Rows the rule booked keep their category."""
    request.params_named()
    with refusals_as(404):
        rules.delete_rule(conn, request.params["id"])
    return {"deleted": request.params["id"]}


def _switch(conn, request: Request, enabled: bool) -> dict:
    request.fields()
    rule = _one_rule(conn, request.params["id"])
    rules.set_enabled(conn, rule["id"], enabled)
    categorized = rules.apply_rules(conn, rule["business_id"])["categorized"] if enabled else 0
    return {"rule": _one_rule(conn, rule["id"]), "categorized": categorized}


@api("POST", "rules/{id}/enable")
def rule_enable(conn, request: Request) -> dict:
    """Let a disabled rule book rows again, and run the rules over the queue, as 'rules enable' does."""
    return _switch(conn, request, True)


@api("POST", "rules/{id}/disable")
def rule_disable(conn, request: Request) -> dict:
    """The rule keeps its place, and the rows it booked stay booked."""
    return _switch(conn, request, False)


@api("POST", "rules/{id}/move")
def rule_move(conn, request: Request) -> dict:
    """Put a rule at a place in the order, counted from 1, or just before another rule."""
    fields = request.fields("to", "before")
    rule = _one_rule(conn, request.params["id"])
    place = rules.move_rule(conn, rule["id"], before=fields.identifier("before"), position=fields.identifier("to"))
    return {"place": place, "rules": rules_of(conn, business_by_id(conn, rule["business_id"]))}


@api("GET", "rules/explain")
def rule_explain(conn, request: Request) -> dict:
    """Each rule of the row's business tried on one row, whether each condition held, and which rule books it."""
    query = request.params_named("transaction")
    transaction_id = query.number("transaction")
    if transaction_id is None:
        raise ApiError(400, "transaction is required")
    with refusals_as(404):
        row = ledger.get_transaction(conn, transaction_id)
    explanation = rules.explain(conn, transaction_id)
    winner = explanation.winner
    return {
        "transaction_id": transaction_id,
        "merchant": row["merchant"],
        "booked_by": row["category_source"],
        "rule_id": row["rule_id"],
        "winner": winner.id if winner else None,
        "trials": [
            {
                "place": trial.place,
                "rule_id": trial.rule.id,
                "enabled": trial.rule.enabled,
                "said": rules.said(conn, trial.rule),
                "conditions": trial.lines,
                "matched": trial.matched,
                "held_back": trial.held_back,
                "verdict": trial.verdict,
            }
            for trial in explanation.trials
        ],
    }


@api("POST", "rules/test")
def rule_test(conn, request: Request) -> dict:
    """Which rule a description would hit, without changing anything. No match is an answer, not an error."""
    fields = request.fields("business_id", "description", "amount", "account_id")
    business = chosen_business(conn, fields.identifier("business_id"))
    description = fields.text("description", required=True) or ""
    amount = fields.amount("amount")
    account = _account_in(conn, business, fields.identifier("account_id"))
    tested = _TEST_AMOUNT_CENTS if amount is None else amount
    merchant = merchant_key(description)
    rule = rules.first_match(
        rules.load_rules(conn, business["id"]), account["id"] if account else -1, description, "", merchant, tested
    )
    data = {"merchant": merchant, "rule": None, "category": None}
    data.update(money("tested_amount", tested))
    if rule is not None:
        category = [c for c in books.list_categories(conn, business["id"], include_retired=True) if c["id"] == rule.category_id][0]
        data["rule"] = {"id": rule.id, "match": rule.match_type, "pattern": rule.pattern, "said": rules.said(conn, rule)}
        data["category"] = category_json(category)
    return data


@api("POST", "rules/apply")
def rule_apply(conn, request: Request) -> dict:
    """Run the rules over the queue; with recheck, over rows rules booked earlier too. Rows booked by hand stay."""
    fields = request.fields("business_id", "recheck")
    business_id = fields.identifier("business_id")
    chosen = [business_by_id(conn, business_id)] if business_id is not None else books.list_businesses(conn)
    recheck = fields.flag("recheck")
    results = []
    for business in chosen:
        outcome = rules.apply_rules(conn, business["id"], recheck=recheck)
        results.append(dict(outcome, business_id=business["id"], business=business["name"]))
    return {"results": results}
