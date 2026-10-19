"""What a file is, decided from its bytes, never from the name or type the browser sent.

Only kinds a workflow reads today are accepted: PDF, PNG, JPEG, and UTF-8 text. Anything else is
refused before it is stored, so nothing the platform cannot read sits in a workspace pretending
to be a document.
"""

from __future__ import annotations

import re
import unicodedata

MAX_FILE_BYTES = 15 * 1024 * 1024
MAX_NAME_CHARS = 200

_TEXT_SNIFF_BYTES = 8192


def sniff(data: bytes, name: str = "") -> str | None:
    """The media type of `data`, or None when it is not a kind we accept."""
    if b"%PDF-" in data[:1024]:
        return "application/pdf"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data and b"\x00" not in data[:_TEXT_SNIFF_BYTES]:
        try:
            data.decode("utf-8")
        except UnicodeDecodeError:
            return None
        return "text/csv" if name.lower().endswith(".csv") else "text/plain"
    return None


def safe_name(name: str) -> str:
    """A display name: no path, no control characters, bounded. Never used as a storage key."""
    name = unicodedata.normalize("NFC", name).replace("\\", "/").rsplit("/", 1)[-1]
    name = re.sub(r"[\x00-\x1f\x7f]", "", name).strip().strip(".")
    return name[:MAX_NAME_CHARS] or "file"
