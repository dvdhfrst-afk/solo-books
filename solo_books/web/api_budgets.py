"""Budgets through the API: listing them, setting a category's budget, and deleting one."""
from __future__ import annotations

from .. import books, budgets
from ..errors import BooksError
from ..periods import check_year
from .api import ApiError, Request, business_by_id, chosen_business, money
from .routes import api


def _year(value) -> int:
    try:
        return check_year(value)
    except BooksError as exc:
        raise ApiError(400, str(exc)) from None


def budget_json(entry: budgets.BudgetYear) -> dict:
    data = {
        "business": entry.business,
        "year": entry.year,
        "code": entry.code,
        "name": entry.name,
        "kind": entry.kind,
        "entered_as": "yearly" if entry.yearly else "monthly",
        "months": [dict(month=month, **money("amount", entry.months[month])) for month in range(1, 13)],
    }
    data.update(money("year_total", entry.total))
    return data


@api("GET", "budgets")
def budget_list(conn, request: Request) -> dict:
    query = request.params_named("business", "year")
    business_id = query.number("business")
    if business_id is not None:
        business_by_id(conn, business_id)
    year = query.number("year", high=9999)
    return {"budgets": [budget_json(entry) for entry in budgets.list_budgets(conn, business_id, _year(year) if year else None)]}


def _target(conn, fields):
    business = chosen_business(conn, fields.identifier("business_id"))
    category = books.find_category(conn, business["id"], fields.text("category", required=True) or "")
    year = fields.identifier("year", required=True)
    return business, category, _year(year)


@api("POST", "budgets")
def budget_set(conn, request: Request) -> dict:
    """Set a category's budget for a year, for every month, or for one month, as 'budget set' does."""
    fields = request.fields("business_id", "category", "year", "yearly", "monthly", "month")
    business, category, year = _target(conn, fields)
    yearly, monthly = fields.amount("yearly"), fields.amount("monthly")
    if (yearly is None) == (monthly is None):
        raise ApiError(400, "give yearly or monthly, one of them")
    month = fields.identifier("month")
    if month is not None and not 1 <= month <= 12:
        raise ApiError(400, "month must be 1 to 12")
    outcome = budgets.set_budget(conn, business, category, year, monthly=monthly, yearly=yearly, month=month)
    found = [e for e in budgets.list_budgets(conn, business["id"], year) if e.code == category["code"]]
    return {"budget": budget_json(found[0]), "spread": outcome["spread"]}


@api("POST", "budgets/delete")
def budget_delete(conn, request: Request) -> dict:
    fields = request.fields("business_id", "category", "year")
    business, category, year = _target(conn, fields)
    total = budgets.delete_budget(conn, business, category, year)
    return dict(deleted={"code": category["code"], "name": category["name"], "year": year}, **money("year_total", total))
