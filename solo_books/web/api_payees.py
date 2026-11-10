"""Tags and payees through the API: listing them, tagging rows, and the payee directory's names, merges and flags."""
from __future__ import annotations

from .. import payees, tags
from ..errors import BooksError
from ..periods import check_year
from .api import ApiError, Request, business_by_id, chosen_business, money, refusals_as
from .routes import api, is_id_list


# Tags

@api("GET", "tags")
def tag_list(conn, request: Request) -> dict:
    """Every tag, with how many rows carry it and the money on them, as 'tag list' shows them."""
    query = request.params_named("business")
    business_id = query.number("business")
    if business_id is not None:
        business_by_id(conn, business_id)
    found = []
    for row in tags.list_tags(conn, business_id):
        entry = {
            "id": row["id"], "business_id": row["business_id"], "business": row["business_name"], "name": row["name"],
            "rows": row["row_count"], "waiting": row["waiting"],
        }
        entry.update(money("money_in", row["money_in"]))
        entry.update(money("money_out", row["money_out"]))
        found.append(entry)
    return {"tags": found}


def _tag_request(request: Request):
    fields = request.fields("tag", "ids")
    name = fields.text("tag", required=True) or ""
    ids = request.body.get("ids")
    if not ids or not is_id_list(ids):
        raise ApiError(400, "ids must be a list of transaction ids")
    return name, ids


@api("POST", "tags/add")
def tag_add(conn, request: Request) -> dict:
    """Put a tag on rows of one business, making the tag if it is new, as 'tag add' does."""
    name, ids = _tag_request(request)
    with refusals_as(422):
        outcome = tags.add_tag(conn, name, ids)
    return {"tag": outcome["tag"], "added": outcome["added"], "already": outcome["already"], "created": outcome["created"]}


@api("POST", "tags/remove")
def tag_remove(conn, request: Request) -> dict:
    name, ids = _tag_request(request)
    outcome = tags.remove_tag(conn, name, ids)
    return {"tag": outcome["tag"], "removed": outcome["removed"], "not_tagged": outcome["not_tagged"]}


# Payees

def payee_json(payee: payees.Payee) -> dict:
    return {
        "name": payee.name,
        "key": payee.key,
        "renamed": payee.renamed,
        "contractor": payee.contractor,
        "spellings": list(payee.merchants),
    }


@api("GET", "payees")
def payee_list(conn, request: Request) -> dict:
    """Each payee with its rows and money per year, as 'payee list' shows them."""
    query = request.params_named("business", "year")
    business = chosen_business(conn, query.number("business"), "business")
    year = query.number("year", low=1, high=9999)
    if year is not None:
        try:
            check_year(year)
        except BooksError as exc:
            raise ApiError(400, str(exc)) from None
    years = {}
    for entry in payees.payee_years(conn, business["id"], year):
        years.setdefault(entry.payee.key, []).append(
            dict(year=entry.year, rows=entry.rows, **money("money_out", entry.money_out), **money("money_in", entry.money_in))
        )
    found = []
    for payee in payees.all_payees(conn, business["id"]):
        if year is not None and payee.key not in years:
            continue
        found.append(dict(payee_json(payee), years=years.get(payee.key, [])))
    return {"business_id": business["id"], "business": business["name"], "payees": found}


def _payee_fields(conn, request: Request, *more: str):
    fields = request.fields("business_id", "payee", *more)
    business = chosen_business(conn, fields.identifier("business_id"))
    return fields, business, fields.text("payee", required=True) or ""


def _shown(conn, business, name: str) -> dict:
    return payee_json(payees.find_payee(conn, business["id"], name))


@api("POST", "payees/rename")
def payee_rename(conn, request: Request) -> dict:
    fields, business, text = _payee_fields(conn, request, "name")
    _, name = payees.rename_payee(conn, business["id"], text, fields.text("name", required=True) or "")
    return {"payee": _shown(conn, business, name)}


@api("POST", "payees/merge")
def payee_merge(conn, request: Request) -> dict:
    """Make one payee another spelling of a second. A contractor flag on the first carries over."""
    fields, business, text = _payee_fields(conn, request, "into")
    outcome = payees.merge_payees(conn, business["id"], text, fields.text("into", required=True) or "")
    return {"payee": _shown(conn, business, outcome["into"].key), "contractor_carried": outcome["contractor_carried"]}


@api("POST", "payees/unmerge")
def payee_unmerge(conn, request: Request) -> dict:
    _, business, text = _payee_fields(conn, request)
    outcome = payees.unmerge_payee(conn, business["id"], text)
    return {
        "payee": _shown(conn, business, outcome["merchant"]),
        "apart_from": outcome["from"],
        "contractor_returned": outcome["contractor_returned"],
    }


@api("POST", "payees/contractor")
def payee_contractor(conn, request: Request) -> dict:
    fields, business, text = _payee_fields(conn, request, "contractor")
    fields.require("contractor")
    payee = payees.set_contractor(conn, business["id"], text, fields.flag("contractor"))
    return {"payee": _shown(conn, business, payee.key)}
