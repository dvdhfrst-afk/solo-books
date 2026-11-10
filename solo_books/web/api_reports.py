"""Reports and settings through the API.

Each report is worked out by report_builder, as the command line works it out, and written by the same format
registry. JSON answers with the shape docs/EXPORTS.md describes; csv, html, markdown and table answer with the file
itself, ready to save. reports/{kind}/document answers with the report laid out in sections and tables, which is
what the Reports page shows.
"""
from __future__ import annotations

from .. import books, config, formats, report_builder
from ..errors import BooksError
from ..formats import json_format, layout
from ..formats.base import Paragraph
from ..periods import check_year
from ..reports import NOT_ADVICE
from ..text import slug
from .api import ApiError, Request, business_by_id
from .routes import Download, api

# Every report the builder knows, with the query parameters each takes besides business and format.
REPORT_PARAMETERS = {
    "pnl": ("period", "by", "tag"),
    "balances": ("period",),
    "schedule-c": ("year", "lines_year", "all_lines"),
    "cashflow": ("period", "by", "tag"),
    "trend": ("years", "tag"),
    "categories": ("period", "tag"),
    "budget": ("period", "tag"),
    "payees": ("year", "limit", "tag"),
    "contractors": ("year", "threshold"),
}
_EXTENSIONS = {"csv": "csv", "html": "html", "markdown": "md", "table": "txt"}
_TYPES = {
    "csv": "text/csv; charset=utf-8",
    "html": "text/html; charset=utf-8",
    "markdown": "text/markdown; charset=utf-8",
    "table": "text/plain; charset=utf-8",
}


def settings_of(request: Request) -> config.Settings:
    """The settings the 'web' command started with, or, for a server started some other way, the settings as they are now."""
    return request.settings if request.settings is not None else config.resolve()


def _year(query, name: str):
    value = query.number(name, high=9999)
    if value is None:
        return None
    try:
        return check_year(value)
    except BooksError as exc:
        raise ApiError(400, str(exc)) from None


def document_json(report) -> dict:
    """The report laid out as the HTML and Markdown formats lay it out: headed sections of lines and text tables."""
    doc = layout.document(report)
    sections = []
    for section in doc.sections:
        blocks = []
        for block in section.blocks:
            if isinstance(block, Paragraph):
                blocks.append({"paragraph": block.text, "warning": block.warning})
            else:
                blocks.append({
                    "headers": block.headers, "rows": block.rows, "numeric": sorted(block.numeric), "styles": block.styles,
                })
        sections.append({"heading": section.heading, "intro": section.intro, "blocks": blocks})
    return {"report": report.kind, "title": doc.title, "sections": sections, "note": NOT_ADVICE}


def answer_report(conn, request: Request, kind: str, page: bool = False):
    query = request.params_named("business", *(() if page else ("format",)), *REPORT_PARAMETERS[kind])
    name = "json" if page else query.text("format") or "json"
    if name not in formats.names():
        raise ApiError(400, f"format must be one of {', '.join(formats.names())}")
    business_id = query.number("business")
    chosen = [business_by_id(conn, business_id)] if business_id is not None else books.list_businesses(conn)
    report = report_builder.build(
        conn,
        kind,
        chosen,
        period=query.text("period"),
        year=_year(query, "year"),
        years=query.text("years"),
        by=query.text("by") or "total",
        tag=query.text("tag"),
        limit=query.number("limit", report_builder.DEFAULT_PAYEE_LIMIT, high=1000),
        threshold=query.text("threshold"),
        lines_year=_year(query, "lines_year"),
        all_lines=query.flag("all_lines"),
        show_date=settings_of(request).show_date,
    )
    if page:
        return document_json(report)
    if name == "json":
        return json_format.payload(report)
    fmt = formats.get(name)
    stem = slug(f"{kind} {report.period.label if report.period else query.text('years') or ''}") or kind
    return Download(f"{stem}.{_EXTENSIONS[name]}", _TYPES[name], fmt.render(report), fmt.byte_order_mark)


def _register(kind: str) -> None:
    @api("GET", f"reports/{kind}")
    def answer(conn, request: Request):
        return answer_report(conn, request, kind)

    @api("GET", f"reports/{kind}/document")
    def laid_out(conn, request: Request):
        return answer_report(conn, request, kind, page=True)


for _kind in REPORT_PARAMETERS:
    _register(_kind)


@api("GET", "reports")
def report_kinds(conn, request: Request) -> dict:
    """The reports there are, the parameters each takes, and the formats each can be written in."""
    request.params_named()
    return {
        "reports": [{"report": kind, "parameters": ["business", "format", *more]} for kind, more in REPORT_PARAMETERS.items()],
        "formats": list(formats.names()),
        "taggable": list(report_builder.TAGGABLE),
    }


@api("GET", "config", needs_conn=False)
def config_show(request: Request) -> dict:
    """Every setting, its value and where the value came from, as 'config list' shows them. Read only."""
    request.params_named()
    settings = settings_of(request)
    return {
        "settings_file": str(settings.path),
        "settings_file_exists": settings.file_exists,
        "profile": settings.profile,
        "profile_from": settings.profile_source or None,
        "settings": [
            {
                "name": setting.name,
                "value": settings[setting.name],
                "shown": setting.show(settings[setting.name]),
                "from": settings.sources[setting.name],
                "means": setting.help,
            }
            for setting in config.SETTINGS
        ],
    }
