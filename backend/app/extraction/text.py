"""The text of a stored document, for checking what a model read against it (ADR-0033).

Only for the check: the model reads the original file. A scanned PDF or an image has no text
layer, so nothing it read can be confirmed and every value is listed for checking.
"""

from __future__ import annotations

import io

from pypdf import PdfReader
from pypdf.errors import PdfReadError

from app.extraction.reader import Document


def text_of(doc: Document) -> str:
    if doc.media_type == "application/pdf":
        try:
            return "\n".join(page.extract_text() or "" for page in PdfReader(io.BytesIO(doc.data)).pages)
        except PdfReadError:
            return ""
    if doc.media_type.startswith("text/"):
        return doc.data.decode("utf-8", errors="replace")
    return ""
