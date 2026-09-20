"""Memory seam — continuity across runs for the same subject (e.g. a returning caller).

Deliberately scoped: this is NOT a general memory store. It remembers how to FIND what a subject
already has — a key, a pointer, a salutation — and never the content itself, which is read live
from the system of record on each run (ADR-0011 D1, keeping ADR-0002 #11 intact).

Async, because every caller of it is: the webhook that recalls before a call and the pipeline that
remembers after one both run on the event loop, and wrapping a database round trip in a thread to
satisfy a synchronous protocol would buy nothing but a thread.
"""

from __future__ import annotations

from typing import Any, Protocol


class MemoryStore(Protocol):
    async def recall(self, subject_id: str) -> dict[str, Any] | None:
        """What is known about a subject, or None when nothing is."""
        ...

    async def remember(self, subject_id: str, summary: dict[str, Any]) -> None:
        """Persist what this run established about the subject, for the next one."""
        ...
