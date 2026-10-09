"""Import orchestration (ADR-0004 D4) — enumerate the source, run one governed contract per
appointment, aggregate a completeness report.

This sits ABOVE the engine: it lists the source work-list, then for each appointment starts a
governed run of the `calendar_import` contract (which re-grounds against the source and enforces
idempotent, verified, append-only copy). It classifies each run's outcome and reports
source-count vs. created + skipped + failed, so nothing is silently dropped.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession

from app.connections.resolve import client_from_connection
from app.control_plane.runtime import Runtime
from app.core.errors import NotFoundError
from app.db import repo
from app.db.models import AgentInstance
from app.providers.thevea import _CANCELLED_STATUSES, _iso_z
from app.providers.thevea import _to_utc  # the one canonical parser for these date strings

# Source statuses meaning "this booking is off" — the doctolib API reports a cancellation as
# `deleted`, healthyfeet as `cancelled`/`rescheduled` (a move books a NEW ref and retires the old).
_GONE_STATUSES = frozenset({"deleted", "cancelled", "canceled", "rescheduled"})


@dataclass
class ImportReport:
    total: int = 0  # appointments ELIGIBLE and attempted (after every filter)
    created: list[str] = field(default_factory=list)  # source refs newly appended
    skipped: list[str] = field(default_factory=list)  # already present (append-only skip)
    failed: list[dict[str, Any]] = field(default_factory=list)  # {ref, status, reason}
    excluded: list[dict[str, str]] = field(default_factory=list)  # {ref, reason} — never imported
    # Written only after EVERY room refused as absent, by bypassing the destination's own
    # working-hours check (ADR-0005 D7). Its own bucket on purpose: an override nobody can see is
    # indistinguishable from a bug, and these are the ones the operator must look at by hand.
    forced: list[str] = field(default_factory=list)
    # Copies already in thevea that no longer match the source: moved since the import, or
    # cancelled there and still live here. Append-only cannot fix either, so the operator must —
    # {kind: moved | cancelled_in_source | unchecked, ref, patient, source_start, dest_start, ...}.
    review: list[dict[str, Any]] = field(default_factory=list)
    # ref -> patient name for every appointment the run saw, so each bucket can be read as people
    # rather than hashes: a `DL-…` ref is unsearchable in either calendar.
    patients: dict[str, str] = field(default_factory=dict)

    @property
    def complete(self) -> bool:
        return self.total == (
            len(self.created) + len(self.forced) + len(self.skipped) + len(self.failed)
        )


async def _bound_connector(
    session: AsyncSession, instance: AgentInstance, role: str, **opts: Any
) -> Any:
    """Build the connector for the instance's `role` connection binding."""
    for binding in instance.connections:
        if binding.role != role:
            continue
        conn = await repo.get_connection(session, binding.connection_id, instance.tenant_id)
        if conn is None:
            break
        # Reuse the same builder as the governed run (default base URL + decrypt) — do NOT
        # reimplement the base-url fallback here (that omission built an empty-URL client).
        return client_from_connection(conn, **opts)
    raise NotFoundError(f"instance has no bound {role} connection")


async def run_import(
    session: AsyncSession,
    runtime: Runtime,
    instance: AgentInstance,
    window: dict[str, Any],
    on_progress: Callable[[ImportReport], Awaitable[None]] | None = None,
) -> ImportReport:
    """List the source appointments for `window` and copy each through the governed contract.

    `on_progress`, if given, is awaited once the eligible total is known and again after each
    appointment — the hook the background worker uses to persist live progress to the job row."""
    source = await _bound_connector(session, instance, "source")
    try:
        appointments = source.list_appointments(window)
    finally:
        source.close()

    report = ImportReport()

    # Window filter: import only bookings whose start date falls in [from, to].
    appointments = [a for a in appointments if _in_window(a, window)]
    report.patients = {a.ref: a.patient for a in appointments if a.patient}
    gone = [a for a in appointments if _source_status(a) in _GONE_STATUSES]

    # SAFETY FILTER (unconditional, VERY IMPORTANT): a real migration copies ONLY confirmed,
    # future appointments — never cancelled/rescheduled/new bookings, never past ones. Every
    # excluded appointment is recorded with its reason so nothing is silently dropped.
    now = datetime.now(timezone.utc)
    eligible: list[Any] = []
    for appt in appointments:
        reason = _exclude_reason(appt, now)
        if reason:
            report.excluded.append({"ref": appt.ref, "reason": reason})
        else:
            eligible.append(appt)
    appointments = eligible

    # Then an optional `limit` (import the first N) — the safety valve for a first real run.
    params = instance.param_values or {}
    limit = _as_int(params.get("limit"))
    if limit:
        appointments = appointments[:limit]
    rooms = _rooms(params)  # balance created appointments across these room ids (round-robin)

    report.total = len(appointments)
    if on_progress:
        await on_progress(report)  # publish the total before the first appointment lands
    for i, appt in enumerate(appointments):
        base_case = {
            "appointment": {"ref": appt.ref, **appt.raw},
            "rooms": rooms,  # the full set to SEARCH for idempotency (room-independent)
            "window": window,
        }
        # Placement ladder (ADR-0005 D6): the round-robin room, then the others if that one is
        # absent, then one forced attempt. Every rung is a FULL governed run, so the idempotency
        # precondition is re-evaluated and a retry cannot produce a duplicate.
        assigned = rooms[i % len(rooms)]
        for room_id, forced in _placement_plan(rooms, assigned):
            result = await runtime.run_instance(
                session, instance, {**base_case, "room_id": room_id, "force": forced}
            )
            status = _overall(result)
            if not _should_try_another_room(status):
                break

        if status == "ok":
            (report.forced if forced else report.created).append(appt.ref)
        elif status == "blocked":
            # The idempotency precondition blocked -> already in thevea -> a skip, not a failure.
            report.skipped.append(appt.ref)
        else:  # rejected | state_unavailable
            reason = next((s.reason for s in result.steps if s.reason), None)
            report.failed.append({"ref": appt.ref, "status": status, "reason": reason})
        if on_progress:
            await on_progress(report)

    # Read-only, after every run: what the skips and the source's cancellations look like in
    # thevea. Same window and rooms the idempotency check searched, so it sees the same copies.
    skipped = set(report.skipped)
    to_check = [a for a in appointments if a.ref in skipped]
    if to_check or gone:
        dest = await _bound_connector(
            session,
            instance,
            "destination",
            window_from=window.get("from"),
            window_until=window.get("to"),
            search_room_ids=rooms,
        )
        try:
            report.review = _review(dest, skipped=to_check, gone=gone)
        finally:
            dest.close()
        if on_progress:
            await on_progress(report)
    return report


def _review(dest: Any, *, skipped: list[Any], gone: list[Any]) -> list[dict[str, Any]]:
    """The copies in thevea that the source has since moved (`skipped`) or cancelled (`gone`).

    Reports only — nothing here writes. A thevea read that fails marks this and every remaining
    appointment `unchecked` rather than clean: a check that could not run must not read as "all in
    order", and retrying a system that just failed only adds load to it.
    """
    review: list[dict[str, Any]] = []
    failure: str | None = None
    for kind, appt in [("moved", a) for a in skipped] + [("cancelled_in_source", a) for a in gone]:
        entry: dict[str, Any] = {"ref": appt.ref, "patient": appt.patient}
        if failure is None:
            try:
                copy = dest.find_appointment(appt.ref)
            except Exception as exc:  # noqa: BLE001 — recorded on the entry, never swallowed
                failure = str(exc)[:300]
        if failure is not None:
            review.append({"kind": "unchecked", **entry, "reason": failure})
            continue
        if copy is None:
            continue  # never imported (or not in this window) — nothing stale to clean up
        try:
            source_start, dest_start = _iso_z(_to_utc(appt.start)), _iso_z(_to_utc(copy.start))
        except Exception:  # noqa: BLE001 — one odd date must not cost the rest of the report
            review.append({"kind": "unchecked", **entry, "reason": "unparseable start date"})
            continue
        times = {"source_start": source_start, "dest_start": dest_start}
        if kind == "moved" and source_start != dest_start:
            review.append({"kind": kind, **entry, **times})
        elif kind == "cancelled_in_source":
            if str((copy.raw or {}).get("status") or "").upper() in _CANCELLED_STATUSES:
                continue  # already cancelled in thevea too
            review.append({"kind": kind, **entry, **times, "source_status": _source_status(appt)})
    return review


def _source_status(appt: Any) -> str:
    return str((appt.raw or {}).get("status") or "").strip().lower()


def _placement_plan(rooms: list[int], assigned: int) -> list[tuple[int, bool]]:
    """The rooms to try for one appointment, as `(room_id, forced)` in order.

    The assigned room first, then the remaining ones (a room refuses only because it is absent —
    another may be free), and finally ONE forced attempt back in the assigned room, so an
    appointment nobody can take still lands somewhere the operator will see it (ADR-0005 D6).
    """
    ordered = [assigned] + [r for r in rooms if r != assigned]
    return [(room, False) for room in ordered] + [(assigned, True)]


def _should_try_another_room(status: str) -> bool:
    """Whether a failed placement is worth retrying in a different room.

    We deliberately do NOT try to identify the destination's `ABWESENHEIT` here: a tool's note
    never reaches the orchestrator — the engine reports a rejected step with the *postcondition's*
    reason (`laufwise/engine/local.py:241`), by design, since the check is what ruled. So the
    orchestrator retries on the only signal it actually has:

    - `rejected`  -> the write did not land. An absent room is the expected cause and another room
                     may well take it; any other cause simply fails again, one room later.
    - `ok`        -> placed.
    - `blocked`   -> already imported (idempotency) — a skip, not a placement problem.
    - `state_unavailable` -> the system is unreachable; another room cannot fix that, and retrying
                     would hammer a system that is already failing.
    """
    return status == "rejected"


def _exclude_reason(appt: Any, now: datetime) -> str | None:
    """Why this source appointment must NOT be imported, or None if it is eligible.

    A migration copies only CONFIRMED, FUTURE bookings. Anything else — a cancelled, rescheduled
    or still-'new' booking, or one whose start is already in the past — is excluded (and reported).
    Conservative on ambiguity: an unparseable start date is excluded, not guessed into the future.
    """
    status = _source_status(appt)
    if status != "confirmed":
        return f"not confirmed (status={status or 'unknown'})"
    try:
        start = _to_utc(appt.start)
    except Exception:
        return "unparseable start date"
    if start < _today_start(now):
        return "in the past"
    return None


def _today_start(now: datetime) -> datetime:
    """Midnight of the practice's current day, in UTC — the cutoff for "already past".

    Not `now`: asking for today means the whole of today. An appointment at 09:00 is still the
    reason an invoice gets written at 11:00, and this import exists to save that retyping
    (ADR-0005) — refusing it because the hour has passed makes the tool useless for exactly the
    appointments the practice is billing. Still bounded to one day, so a wide window cannot
    back-fill months of history by accident.

    The practice's day, not UTC's: at 01:00 Berlin those are different dates, and the operator
    means the one on their wall.
    """
    berlin = now.astimezone(ZoneInfo("Europe/Berlin"))
    return berlin.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)


def _as_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _rooms(params: dict[str, Any]) -> list[int]:
    """The destination room ids to balance appointments across (round-robin). From the instance's
    `rooms` param (comma-separated, e.g. "208413,208414"); defaults to MA 1 only."""
    raw = str(params.get("rooms") or "208413")
    ids = [_as_int(x) for x in raw.replace(" ", "").split(",") if x]
    return [i for i in ids if i] or [208413]


def _in_window(appt: Any, window: dict[str, Any]) -> bool:
    """Keep an appointment if its start date is within [from, to] (either bound optional).
    Dates compare on the YYYY-MM-DD prefix, so window bounds are plain dates."""
    lo, hi = window.get("from"), window.get("to")
    if not lo and not hi:
        return True
    day = (str(appt.start)[:10]) if appt.start else ""
    if not day:
        return True
    if lo and day < str(lo)[:10]:
        return False
    if hi and day > str(hi)[:10]:
        return False
    return True


def _overall(result: Any) -> str:
    """The run's overall status from its step results (worst-of)."""
    order = ["state_unavailable", "blocked", "rejected"]
    statuses = {s.status.value if hasattr(s.status, "value") else s.status for s in result.steps}
    for bad in order:
        if bad in statuses:
            return bad
    return "ok"