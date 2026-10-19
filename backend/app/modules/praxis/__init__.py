"""The medical-practice vertical: a voice receptionist that books into the practice's calendar,
and the workflows that keep that calendar in step with the others (ADR-0028).

Its code still lives where it grew (`app/workloads/conversational`, `app/sync`, `app/connectors`);
it moves here step by step (ADR-0022, build plan 3b). What it declares below is already true.
"""

from app.kernel import Module

MODULE = Module(
    key="praxis",
    label="Medical practice",
    summary="A voice receptionist that answers and books into your practice calendar, and "
    "workflows that import and mirror appointments between calendars.",
    pitch=("Built for your practice", "Every booking checked.", "Every conversation traceable."),
    intro="A helpful first conversation. A booking your practice can trust.",
    templates=frozenset(
        {
            "availability_mirror",
            "calendar_import",
            "praxis_appointment",
            "voice_appointment",
            "voice_appointment_cancel",
            "voice_appointment_reschedule",
        }
    ),
    surfaces=frozenset({"voice_agents"}),
)
