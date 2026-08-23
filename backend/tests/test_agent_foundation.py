"""Release B unit proof for additive task/conversation records and timeline DTOs."""

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

from app.schemas.conversation import ConversationDetail
from app.schemas.task import TaskDetail


def _event(seq: int, kind: str, payload: dict) -> SimpleNamespace:
    return SimpleNamespace(
        seq=seq,
        kind=kind,
        payload=payload,
        created_at=datetime.now(timezone.utc),
    )


def test_task_timeline_preserves_ordered_harness_evidence() -> None:
    task = SimpleNamespace(
        id=uuid.uuid4(),
        instance_id=uuid.uuid4(),
        task_type="calendar_import",
        trigger_type="manual",
        status="pending",
        context={"legacy": True},
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
        events=[
            _event(0, "triggered", {"source": "instance_import"}),
            _event(1, "run_linked", {"run_id": uuid.uuid4().hex}),
        ],
    )

    detail = TaskDetail.of(task)

    assert [event.seq for event in detail.events] == [0, 1]
    assert [event.kind for event in detail.events] == ["triggered", "run_linked"]


def test_conversation_timeline_can_record_turn_latency_without_raw_audio() -> None:
    conversation = SimpleNamespace(
        id=uuid.uuid4(),
        instance_id=uuid.uuid4(),
        channel="voice",
        direction="inbound",
        status="active",
        external_id="provider-call-id",
        metadata_={"locale": "de-DE"},
        started_at=datetime.now(timezone.utc),
        ended_at=None,
        events=[_event(0, "turn_latency", {"turn": 0, "first_audio_ms": 640})],
    )

    detail = ConversationDetail.of(conversation)

    assert detail.events[0].payload == {"turn": 0, "first_audio_ms": 640}
    assert detail.metadata == {"locale": "de-DE"}
