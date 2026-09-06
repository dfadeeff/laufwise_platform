"""Render one saved call as a self-contained Markdown example.

A conversation in the database is the audit record: every turn, every tool call with its real
result, the governed run ids, the summary the practice was sent. That is the right shape for
answering "what did this agent actually do" and the wrong shape for keeping an example — you
cannot paste JSONB into a review, an eval scenario, or a message to the practice.

So this is the export. It keeps the one distinction the whole screen exists to make: what the
agent SAID and what it DID are rendered separately, and a tool's real result is never collapsed
into the sentence the caller heard. An agent that says "you're booked" while the engine blocked
the write reads perfectly in a transcript alone; it does not read perfectly here.

Deliberately NOT redacted. This is the protected system — the same place the transcript already
lives, under the same 30-day retention (spec §7). What leaves by email is the restricted view
(`notifications.body_for`), and the two must not be confused: this one is for the people who are
allowed to see everything.
"""

from __future__ import annotations

import json
from typing import Any

# Long tool results are the useful part of a call, but a 60-line payload between two sentences
# buries the conversation. Same reasoning as the timeline's max-height, applied to text.
_MAX_RESULT_LINES = 24


def _fence(value: Any) -> str:
    body = json.dumps(value, indent=2, ensure_ascii=False, default=str)
    lines = body.splitlines()
    if len(lines) > _MAX_RESULT_LINES:
        body = "\n".join([*lines[:_MAX_RESULT_LINES], f"… {len(lines) - _MAX_RESULT_LINES} more lines"])
    return f"```json\n{body}\n```"


def _turn(payload: dict[str, Any]) -> str:
    role = str(payload.get("role", "?"))
    speaker = {"caller": "**Caller**", "agent": "**Agent**"}.get(role, f"**{role}**")
    return f"{speaker} — {str(payload.get('text', '')).strip()}"


def _tool(payload: dict[str, Any]) -> str:
    name = payload.get("tool", "?")
    result = payload.get("result") or {}
    status = result.get("status")
    run = payload.get("run_id")
    head = f"> `{name}`" + (f" → **{status}**" if status else "")
    if run:
        head += f"  ·  governed run `{str(run)[:8]}`"
    args = {k: v for k, v in (payload.get("arguments") or {}).items() if v not in (None, "", [])}
    parts = [head]
    if args:
        parts.append("> " + "  ".join(f"`{k}={v}`" for k, v in args.items()))
    parts.append(_fence(result))
    return "\n".join(parts)


def as_markdown(conversation: Any) -> str:
    """The whole call, in the order it happened."""
    meta = conversation.metadata_ or {}
    started = conversation.started_at
    lines = [
        f"# Call {conversation.id.hex[:8]}",
        "",
        f"- **When** — {started:%Y-%m-%d %H:%M} → "
        + (f"{conversation.ended_at:%H:%M}" if conversation.ended_at else "still open"),
        f"- **Channel** — {conversation.channel} ({conversation.direction})",
        f"- **Language** — {meta.get('language', 'unknown')}",
        # Which calendar it booked into is the first thing anyone asks of a saved call: a
        # rehearsal in the sandbox and a real practice booking read identically otherwise.
        f"- **Calendar** — {meta.get('calendar', 'unknown')}",
        f"- **Status** — {conversation.status}",
        f"- **Call id** — `{conversation.id.hex}`",
    ]
    if caller := meta.get("from"):
        lines.append(f"- **From** — {caller}")
    lines += ["", "## Timeline", ""]

    for event in conversation.events:
        if event.kind == "turn":
            lines += [_turn(event.payload), ""]
        elif event.kind == "tool_call":
            lines += [_tool(event.payload), ""]
        elif event.kind == "call_summary":
            payload = event.payload or {}
            summary, delivery = payload.get("summary") or {}, payload.get("delivery") or {}
            lines += [
                "## Summary sent to the practice",
                "",
                f"- **Outcome** — {summary.get('outcome', '?')}",
                f"- **Staff action required** — {'yes' if summary.get('staff_action_required') else 'no'}",
                "- **Email** — "
                + (
                    f"sent to {', '.join(delivery.get('recipients', []))}"
                    if delivery.get("sent")
                    else f"NOT sent ({delivery.get('reason', 'unknown')})"
                ),
                "",
                _fence(summary),
                "",
            ]
        else:
            lines += [f"> _{event.kind}_", _fence(event.payload), ""]
    return "\n".join(lines).rstrip() + "\n"
