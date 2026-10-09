"""The import report's review section: appointments the append-only import can't fix by itself.

A skip only says "this ref is already in thevea". When the source has since moved the appointment,
or cancelled it, the copy in thevea is stale and nothing else in the report would say so — the
operator has to find it by hand, and needs the patient's name to do that.
"""

from __future__ import annotations

from app.connectors.base import Appointment
from app.sync.orchestrator import _review


class _Dest:
    def __init__(self, store: dict[str, Appointment], fails: bool = False):
        self._store, self._fails = store, fails
        self.calls = 0

    def find_appointment(self, ref):
        self.calls += 1
        if self._fails:
            raise RuntimeError("thevea unreachable")
        return self._store.get(ref)


def _src(ref, start, name="Anita Liutvinskaia", status="confirmed") -> Appointment:
    return Appointment(ref=ref, start=start, patient=name, raw={"status": status})


def _dst(ref, start, status=None) -> Appointment:
    return Appointment(ref=ref, start=start, raw={"from": start, "status": status})


def test_moved_in_source_is_reported_with_patient_and_both_times():
    dest = _Dest({"DL-1": _dst("DL-1", "2026-10-09T13:00:00Z")})
    review = _review(dest, skipped=[_src("DL-1", "2026-10-09T17:30:00.000+02:00")], gone=[])
    assert review == [
        {
            "kind": "moved",
            "ref": "DL-1",
            "patient": "Anita Liutvinskaia",
            "source_start": "2026-10-09T15:30:00.000Z",
            "dest_start": "2026-10-09T13:00:00.000Z",
        }
    ]


def test_same_instant_in_another_notation_is_not_a_move():
    dest = _Dest({"DL-1": _dst("DL-1", "2026-10-09T15:30:00Z")})
    assert _review(dest, skipped=[_src("DL-1", "2026-10-09T17:30:00.000+02:00")], gone=[]) == []


def test_cancelled_in_source_but_live_in_thevea_is_reported():
    dest = _Dest({"DL-2": _dst("DL-2", "2026-10-09T09:00:00Z")})
    gone = [_src("DL-2", "2026-10-09T11:00:00.000+02:00", name="Eva Muster", status="deleted")]
    assert _review(dest, skipped=[], gone=gone) == [
        {
            "kind": "cancelled_in_source",
            "ref": "DL-2",
            "patient": "Eva Muster",
            "source_start": "2026-10-09T09:00:00.000Z",
            "dest_start": "2026-10-09T09:00:00.000Z",
            "source_status": "deleted",
        }
    ]


def test_cancelled_on_both_sides_or_never_imported_is_not_reported():
    dest = _Dest({"DL-2": _dst("DL-2", "2026-10-09T09:00:00Z", status="ABGESAGT")})
    gone = [
        _src("DL-2", "2026-10-09T11:00:00+02:00", status="deleted"),
        _src("DL-3", "2026-10-09T12:00:00+02:00", status="deleted"),
    ]
    assert _review(dest, skipped=[], gone=gone) == []


def test_an_unparseable_date_marks_only_that_appointment_unchecked():
    dest = _Dest({"DL-2": _dst("DL-2", "2026-10-09T09:00:00Z"), "DL-1": _dst("DL-1", "2026-10-09T13:00:00Z")})
    review = _review(
        dest,
        skipped=[_src("DL-1", "2026-10-09T17:30:00+02:00")],
        gone=[_src("DL-2", "not a date", status="deleted")],
    )
    assert [(r["kind"], r["ref"]) for r in review] == [("moved", "DL-1"), ("unchecked", "DL-2")]


def test_unreadable_destination_is_reported_as_unchecked_not_as_clean():
    dest = _Dest({}, fails=True)
    review = _review(
        dest,
        skipped=[_src("DL-1", "2026-10-09T17:30:00+02:00")],
        gone=[_src("DL-2", "2026-10-09T11:00:00+02:00", name="Eva Muster", status="deleted")],
    )
    assert [(r["kind"], r["ref"], r["patient"]) for r in review] == [
        ("unchecked", "DL-1", "Anita Liutvinskaia"),
        ("unchecked", "DL-2", "Eva Muster"),
    ]
    assert all("thevea unreachable" in r["reason"] for r in review)
    assert dest.calls == 1  # gave up after the first failure instead of hammering thevea
