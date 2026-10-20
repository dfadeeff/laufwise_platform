"""A workflow's letter as a PDF (ADR-0037), laid out after DIN 5008: letterhead, the sender line
above the recipient, the date, the subject, the text, and the enclosure on its own pages.

It prints the letter's checked text and nothing else: no figure is computed or formatted here,
so the PDF says exactly what the amount check approved (ADR-0034). The date is the day it is
rendered, which is the day it is sent. reportlab's built-in fonts cover the characters a German
letter needs (€, „ “, –, §, ²); a character outside that set would be refused when drawn.
"""

from __future__ import annotations

import io
import textwrap
from datetime import date, datetime
from zoneinfo import ZoneInfo
from xml.sax.saxutils import escape

from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (
    BaseDocTemplate,
    Frame,
    NextPageTemplate,
    PageBreak,
    PageTemplate,
    Paragraph,
    Preformatted,
    Spacer,
)

from app.kernel.workflows import Draft

_W, _H = A4
_LEFT, _RIGHT, _BOTTOM = 25 * mm, 20 * mm, 22 * mm
_TEXT_WIDTH = _W - _LEFT - _RIGHT
# DIN 5008 form B: the address field starts 45 mm from the top, the text below the info block.
_ADDRESS_TOP = 45 * mm
_FIRST_TEXT_TOP = 98 * mm

_BODY = ParagraphStyle("body", fontName="Helvetica", fontSize=10.5, leading=15)
_SUBJECT = ParagraphStyle("subject", parent=_BODY, fontName="Helvetica-Bold", spaceAfter=10)
_HEADING = ParagraphStyle("heading", parent=_BODY, fontName="Helvetica-Bold", spaceAfter=8)
_MONO_MAX, _MONO_MIN = 8.5, 6.5  # points; a Courier character is 0.6 of its size wide


def _paragraphs(text: str) -> list[Paragraph]:
    blocks = [b for b in text.strip().split("\n\n") if b.strip()]
    flow: list = []
    for block in blocks:
        flow.append(Paragraph(escape(block).replace("\n", "<br/>"), _BODY))
        flow.append(Spacer(1, 9))
    return flow


def _enclosure(text: str) -> Preformatted:
    """Fixed-width, sized to the longest line down to a minimum; a line longer still wraps with
    its indentation kept, so a column of figures stays a column and nothing is cut off."""
    lines = text.rstrip("\n").split("\n")
    longest = max((len(line) for line in lines), default=1)
    size = max(_MONO_MIN, min(_MONO_MAX, _TEXT_WIDTH / (0.6 * longest)))
    fits = int(_TEXT_WIDTH / (0.6 * size))
    wrapped: list[str] = []
    for line in lines:
        if len(line) <= fits:
            wrapped.append(line)
            continue
        indent = " " * (len(line) - len(line.lstrip()))
        wrapped += textwrap.wrap(line, fits, subsequent_indent=indent, break_long_words=True) or [""]
    style = ParagraphStyle("mono", fontName="Courier", fontSize=size, leading=size * 1.35)
    return Preformatted("\n".join(wrapped), style)


def today() -> date:
    """The date a letter carries: today in Germany, not on the server's clock (UTC on Railway),
    so a letter sent at half past midnight is not dated the day before."""
    return datetime.now(ZoneInfo("Europe/Berlin")).date()


def letter_pdf(draft: Draft, on: date) -> bytes:
    """The letter, and its enclosure if it has one, as one PDF."""
    buffer = io.BytesIO()

    def footer(canvas, doc) -> None:
        canvas.saveState()
        canvas.setFont("Helvetica", 8)
        canvas.setFillGray(0.4)
        line = " · ".join(x for x in (draft.sender, draft.sender_address) if x)
        if line:
            canvas.drawString(_LEFT, 12 * mm, line)
        canvas.drawRightString(_W - _RIGHT, 12 * mm, f"Seite {doc.page}")
        canvas.restoreState()

    def first_page(canvas, doc) -> None:
        canvas.saveState()
        top = _H - 20 * mm
        if draft.sender:
            canvas.setFont("Helvetica-Bold", 13)
            canvas.drawString(_LEFT, top, draft.sender)
        if draft.sender_address:
            canvas.setFont("Helvetica", 9)
            canvas.drawString(_LEFT, top - 5.5 * mm, draft.sender_address)
        # The sender line above the address field, and the recipient.
        y = _H - _ADDRESS_TOP
        sender_line = ", ".join(x for x in (draft.sender, draft.sender_address) if x)
        if sender_line:
            canvas.setFont("Helvetica", 7)
            canvas.drawString(_LEFT, y, sender_line)
        canvas.setFont("Helvetica", 10.5)
        canvas.drawString(_LEFT, y - 8 * mm, draft.recipient)
        canvas.drawRightString(_W - _RIGHT, _H - 90 * mm, f"{on:%d.%m.%Y}")
        canvas.restoreState()
        footer(canvas, doc)

    doc = BaseDocTemplate(
        buffer,
        pagesize=A4,
        title=draft.title,
        author=draft.sender,
        leftMargin=_LEFT,
        rightMargin=_RIGHT,
        bottomMargin=_BOTTOM,
    )
    # No frame padding: the text lines up with the letterhead and the address at the left margin.
    flush = {"leftPadding": 0, "rightPadding": 0, "topPadding": 0, "bottomPadding": 0}
    first = Frame(_LEFT, _BOTTOM, _TEXT_WIDTH, _H - _FIRST_TEXT_TOP - _BOTTOM, id="first", **flush)
    later = Frame(_LEFT, _BOTTOM, _TEXT_WIDTH, _H - 20 * mm - _BOTTOM, id="later", **flush)
    doc.addPageTemplates(
        [
            PageTemplate(id="first", frames=[first], onPage=first_page),
            PageTemplate(id="later", frames=[later], onPage=footer),
        ]
    )
    story: list = [NextPageTemplate("later"), Paragraph(escape(draft.title), _SUBJECT)]
    story += _paragraphs(draft.text)
    if draft.enclosure:
        story += [
            PageBreak(),
            Paragraph(escape(f"Anlage: {draft.enclosure_name or 'Aufstellung'}"), _HEADING),
            _enclosure(draft.enclosure),
        ]
    doc.build(story)
    return buffer.getvalue()
