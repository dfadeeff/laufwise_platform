"""Approval endpoints — the human-in-the-loop gate for risky steps.

When a run blocks on an approval, the operator console resolves it here. Wiring TODO:
release a pending approval (signal/queue) so the runtime can proceed or halt.
"""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter()


@router.get("")
def list_pending() -> list[dict]:
    """Pending approvals awaiting an operator decision. Stub until wired."""
    return []