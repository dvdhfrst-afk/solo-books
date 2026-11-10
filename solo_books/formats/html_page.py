"""One self-contained printable page. No scripts, fonts or images from anywhere, and every value escaped.

The page's content security policy allows nothing but its own inline style, so even text that slipped past the
escaping could not load or run anything.
"""
from __future__ import annotations

from html import escape
from typing import List

from ..reports import NOT_ADVICE
from .base import GENERATOR, Document, Format, Paragraph, Report, Table
from .layout import document

_STYLE = """
:root { color-scheme: light; }
body { font: 13px/1.45 system-ui, -apple-system, "Segoe UI", sans-serif; color: #1d232a; background: #fbfaf7;
  margin: 24px auto; max-width: 980px; padding: 0 16px; }
h1 { font-size: 22px; margin: 0 0 4px; }
h2 { font-size: 17px; margin: 28px 0 4px; border-bottom: 1px solid #c9c4b8; padding-bottom: 2px; }
h3 { font-size: 14px; margin: 20px 0 4px; }
p { margin: 4px 0; }
p.intro { color: #4a525b; }
p.warning { color: #8a2c0d; font-weight: 600; }
table { border-collapse: collapse; margin: 10px 0; width: 100%; }
th, td { padding: 3px 8px; border-bottom: 1px solid #e4e0d6; text-align: left; vertical-align: top; }
th { border-bottom: 1px solid #8f897c; font-weight: 600; }
td.n, th.n { text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; }
tr.group td { font-weight: 600; padding-top: 10px; border-bottom: none; }
tr.total td { font-weight: 600; }
footer { margin-top: 32px; padding-top: 6px; border-top: 1px solid #c9c4b8; color: #4a525b; font-size: 12px; }
@page { margin: 14mm; }
@media print {
  body { background: none; margin: 0; max-width: none; font-size: 10.5px; }
  h2 { break-after: avoid; }
  tr { break-inside: avoid; }
  section { break-inside: auto; }
}
"""

_POLICY = "default-src 'none'; style-src 'unsafe-inline'; img-src 'none'; form-action 'none'; base-uri 'none'"


def _cell(text: str, tag: str, numeric: bool) -> str:
    """Leading spaces mark a row nested under the one above. HTML would drop them, so they become indent."""
    stripped = text.lstrip(" ")
    indent = (len(text) - len(stripped)) // 2
    attributes = ' class="n"' if numeric else ""
    if indent and not numeric:
        attributes += f' style="padding-left: {8 + 16 * indent}px"'
    return f"<{tag}{attributes}>{escape(stripped)}</{tag}>"


def _table(block: Table) -> str:
    head = "".join(_cell(h, "th", i in block.numeric) for i, h in enumerate(block.headers))
    rows = []
    for cells, style in zip(block.rows, block.styles):
        css = f' class="{style}"' if style else ""
        rows.append(f"<tr{css}>" + "".join(_cell(c, "td", i in block.numeric) for i, c in enumerate(cells)) + "</tr>")
    return f"<table>\n<thead><tr>{head}</tr></thead>\n<tbody>\n" + "\n".join(rows) + "\n</tbody>\n</table>"


def _document(doc: Document, level: int) -> List[str]:
    """level 2 gives each section an h2; inside a page of several documents, sections drop to h3."""
    out: List[str] = []
    if level == 3:
        out.append(f"<h2>{escape(doc.title)}</h2>")
    for section in doc.sections:
        out.append("<section>")
        out.append(f"<h{level}>{escape(section.heading)}</h{level}>")
        out.extend(f'<p class="intro">{escape(line)}</p>' for line in section.intro)
        for block in section.blocks:
            if isinstance(block, Paragraph):
                if block.warning:
                    out.append(f'<p class="warning">Warning: {escape(block.text)}</p>')
                else:
                    out.append(f"<p>{escape(block.text)}</p>")
            else:
                out.append(_table(block))
        out.append("</section>")
    return out


def page(title: str, documents: List[Document]) -> str:
    """A whole page. One document puts its sections straight under the title; several each get a heading."""
    level = 2 if len(documents) == 1 else 3
    body: List[str] = []
    for doc in documents:
        body.extend(_document(doc, level))
    return "\n".join(
        [
            "<!doctype html>",
            '<html lang="en">',
            "<head>",
            '<meta charset="utf-8">',
            f'<meta http-equiv="Content-Security-Policy" content="{_POLICY}">',
            '<meta name="viewport" content="width=device-width, initial-scale=1">',
            f"<title>{escape(title)}</title>",
            f"<style>{_STYLE}</style>",
            "</head>",
            "<body>",
            f"<h1>{escape(title)}</h1>",
            *body,
            f"<footer><p><strong>{escape(NOT_ADVICE)}</strong></p><p>Written by {escape(GENERATOR)}.</p></footer>",
            "</body>",
            "</html>",
            "",
        ]
    )


class HtmlFormat(Format):
    name = "html"
    help = "one printable page, with nothing loaded from anywhere else"

    def render(self, report: Report) -> str:
        doc = document(report)
        return page(doc.title, [doc])


FORMAT = HtmlFormat()
