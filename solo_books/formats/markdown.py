"""Markdown, for notes and wikis. Text from the books is escaped so it shows as written and never as markup."""
from __future__ import annotations

import re
from typing import List

from ..reports import NOT_ADVICE
from .base import GENERATOR, Format, Paragraph, Report, Table
from .layout import document

# Characters that mean something in Markdown or in the HTML it may become. A backslash makes each one plain.
_SPECIAL = re.compile(r"([\\`*_{}\[\]<>()#+!|~&])")
_NO_BREAK_SPACE = chr(0xA0)


def escape(text: str) -> str:
    """Plain text that shows as written. A line break would end a table row, so it becomes a space."""
    return _SPECIAL.sub(r"\\\1", " ".join(text.splitlines()))


def _cell(text: str, bold: bool) -> str:
    """A nested row keeps its indent as non-breaking spaces, which Markdown would otherwise drop."""
    stripped = text.lstrip(" ")
    shown = escape(stripped)
    if bold and shown:
        shown = f"**{shown}**"
    return _NO_BREAK_SPACE * (len(text) - len(stripped)) + shown


def _table(block: Table) -> List[str]:
    lines = ["| " + " | ".join(escape(h) or " " for h in block.headers) + " |"]
    lines.append("|" + "|".join("---:" if i in block.numeric else "---" for i in range(len(block.headers))) + "|")
    for cells, style in zip(block.rows, block.styles):
        lines.append("| " + " | ".join(_cell(c, bool(style)) for c in cells) + " |")
    return lines


class MarkdownFormat(Format):
    name = "markdown"
    help = "Markdown tables, for notes and wikis"

    def render(self, report: Report) -> str:
        doc = document(report)
        lines = [f"# {escape(doc.title)}", ""]
        for section in doc.sections:
            lines.extend([f"## {escape(section.heading)}", ""])
            lines.extend(escape(line) + "  " for line in section.intro)
            lines.append("")
            for block in section.blocks:
                if isinstance(block, Paragraph):
                    text = escape(block.text)
                    lines.extend([f"**Warning:** {text}" if block.warning else text, ""])
                else:
                    lines.extend(_table(block) + [""])
        lines.extend([f"**{escape(NOT_ADVICE)}**", "", f"Written by {escape(GENERATOR)}.", ""])
        return "\n".join(lines)


FORMAT = MarkdownFormat()
