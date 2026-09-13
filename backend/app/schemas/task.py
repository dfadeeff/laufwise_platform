"""Read models for durable operational work and its audit timeline."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel


class TaskEventOut(BaseModel):
    seq: int
    kind: str
    payload: dict[str, Any]
    created_at: datetime


class TaskSummary(BaseModel):
    task_id: str
    instance_id: str
    task_type: str
    trigger_type: str
    status: str
    context: dict[str, Any]
    created_at: datetime
    updated_at: datetime

    @classmethod
    def of(cls, task) -> "TaskSummary":
        return cls(
            task_id=task.id.hex,
            instance_id=task.instance_id.hex,
            task_type=task.task_type,
            trigger_type=task.trigger_type,
            status=task.status,
            context=task.context,
            created_at=task.created_at,
            updated_at=task.updated_at,
        )


class TaskDetail(TaskSummary):
    events: list[TaskEventOut]

    @classmethod
    def of(cls, task) -> "TaskDetail":
        summary = TaskSummary.of(task)
        return cls(
            **summary.model_dump(),
            events=[
                TaskEventOut(
                    seq=event.seq,
                    kind=event.kind,
                    payload=event.payload,
                    created_at=event.created_at,
                )
                for event in task.events
            ],
        )
