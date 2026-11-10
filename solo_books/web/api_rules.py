"""Categorization rules through the API: listing, adding, deleting, testing a description, and running them."""
from __future__ import annotations

from .. import books, rules
from ..text import merchant_key
from .api import ApiError, Request, account_by_id, business_by_id, category_json, chosen_business, money, refusals_as
from .routes import api

# 'rules test' without an amount tries a dollar of money out.
_TEST_AMOUNT_CENTS = -100


def _rule_json(rule, business) -> dict:
    data = {
        "id": rule["id"],
        "business_id": business["id"],
        "business": business["name"],
        "priority": rule["priority"],
        "match": rule["match_type"],
        "pattern": rule["pattern"],
        "category": {"id": rule["category_id"], "code": rule["category_code"], "name": rule["category_name"]},
        "direction": rule["direction"],
        "account_id": rule["account_id"],
        "account": rule["account_name"],
        "rows": rule["hits"],
    }
    data.update(money("min", rule["min_cents"]))
    data.update(money("max", rule["max_cents"]))
    return data


@api("GET", "rules")
def rule_list(conn, request: Request) -> dict:
    """In the order they are tried. Without business=ID, every business's rules, business by business."""
    query = request.params_named("business")
    business_id = query.number("business")
    chosen = [business_by_id(conn, business_id)] if business_id is not None else books.list_businesses(conn)
    return {"rules": [_rule_json(rule, business) for business in chosen for rule in rules.list_rules(conn, business["id"])]}


def _account_in(conn, business, account_id):
    if account_id is None:
        return None
    account = account_by_id(conn, account_id)
    if account["business_id"] != business["id"]:
        raise ApiError(422, f"account {account['name']} belongs to another business")
    return account


@api("POST", "rules")
def rule_add(conn, request: Request):
    """Add a rule and run the rules over the queue, as 'rules add' does."""
    fields = request.fields("business_id", "match", "pattern", "category", "priority", "direction", "min", "max", "account_id")
    business = chosen_business(conn, fields.identifier("business_id"))
    category = books.find_category(conn, business["id"], fields.text("category", required=True) or "")
    account = _account_in(conn, business, fields.identifier("account_id"))
    rule_id = rules.add_rule(
        conn,
        business["id"],
        fields.text("match", required=True) or "",
        fields.text("pattern", required=True) or "",
        category,
        priority=fields.number("priority", 100),
        account_id=account["id"] if account else None,
        direction=fields.text("direction"),
        min_cents=fields.amount("min"),
        max_cents=fields.amount("max"),
    )
    outcome = rules.apply_rules(conn, business["id"])
    added = [rule for rule in rules.list_rules(conn, business["id"]) if rule["id"] == rule_id][0]
    return 201, {"rule": _rule_json(added, business), "categorized": outcome["categorized"]}


@api("DELETE", "rules/{id}")
def rule_delete(conn, request: Request) -> dict:
    """Rows the rule booked keep their category."""
    request.params_named()
    with refusals_as(404):
        rules.delete_rule(conn, request.params["id"])
    return {"deleted": request.params["id"]}


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
        data["rule"] = {"id": rule.id, "match": rule.match_type, "pattern": rule.pattern}
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
