"""The summary email sent after every call (spec §3.9).

Two design decisions carry the whole module.

**It is not a tool.** The spec says "after every accepted call without exception" — after
bookings, after callbacks, after questions, after calls that went nowhere, after technical
failures. A model that can be asked to send it is a model that can forget to, and the one call it
forgets is the one the practice most needed to hear about. So the platform sends it when the call
ends, from what the session actually recorded, and the agent has no say in the matter.

**It says less than we know, on purpose.** The mail leaves the protected system, so the full
transcript, the audio (there is none — spec §4.1), the date of birth and any description of a
condition stay behind. What travels is an outcome, a name, an appointment and a `call_id`; the
`call_id` and the conversation id are enough for a member of staff to open the real record inside
the system, which is where the sensitive detail belongs.

Transport is the kernel's (`app.notifications.mail`, ADR-0024), configured or absent; the practice
only sets how it appears (sender name, reply-to) and who receives it. Absent is a supported state:
a deployment whose mail processor's AVV/DPA is not yet signed (spec §7) can run the agent and see
in the log exactly what would have been sent, rather than have the feature silently disabled.
"""

from __future__ import annotations

import asyncio
import logging
import smtplib
import uuid
from typing import Any

from app.config import settings
from app.notifications import mail
from app.workloads.conversational.practice import Practice, load_practice

log = logging.getLogger(__name__)

# Waits before the second and third attempt when a failure is temporary (#141). Short: this runs
# in the call's close-out, and a server still down after ten seconds is staff's to hear about.
RETRY_DELAYS = (2.0, 8.0)


def is_temporary(error: BaseException) -> bool:
    """Whether another attempt could succeed: the server was unreachable, hung up, or answered 4xx
    ("try again later"). A 5xx — the live test's `530 Authentication Required`, a refused sender
    or recipient — is a refusal that no retry fixes, and anything else is a bug."""
    if isinstance(error, smtplib.SMTPRecipientsRefused):
        codes = [code for code, _ in error.recipients.values()]
        return bool(codes) and all(400 <= code < 500 for code in codes)
    if isinstance(error, smtplib.SMTPResponseException):
        return 400 <= error.smtp_code < 500
    if isinstance(error, smtplib.SMTPServerDisconnected):
        return True
    if isinstance(error, smtplib.SMTPException):
        return False
    # Refused or timed-out connections, DNS hiccups: the network, not the message.
    return isinstance(error, OSError)


def subject_for(summary: dict[str, Any]) -> str:
    """`[Voice Agent] {ACTION} — {patient_name_or_unknown} — {call_date_time}` (spec §3.9)."""
    return (
        f"[Voice Agent] {summary['outcome']} — "
        f"{summary.get('patient_name') or 'unbekannt'} — {summary['at']}"
    )


def body_for(
    summary: dict[str, Any],
    *,
    language: str,
    caller_number: str | None,
    conversation_id: uuid.UUID | None,
    practice: Practice | None = None,
) -> str:
    """The summary body. Every line here is on spec §3.9's list, and nothing else is.

    Notably absent and deliberately so: the transcript, the date of birth, and any description of
    what is wrong with the patient's feet. `call_id` locates all of that inside the system.
    """
    practice = practice or load_practice()
    lines = [
        f"Anruf:            {summary['at']}",
        f"Rufnummer:        {caller_number or 'unbekannt'}",
        f"Sprache:          {language}",
        f"Ergebnis:         {summary['outcome']}",
        f"Patient:          {summary.get('patient_name') or 'unbekannt'}",
    ]

    if appointment := summary.get("appointment"):
        lines.append(
            f"Neuer Termin:     {appointment['start']} · {appointment['service']} "
            f"· {appointment.get('resource') or ''}".rstrip(" ·")
        )
    if moved := summary.get("moved"):
        lines.append(f"Verschoben von:   {moved['from']}")
        lines.append(f"Verschoben auf:   {moved['to']}")
    if cancelled := summary.get("cancelled"):
        lines.append(f"Abgesagt:         {cancelled['start']}")
        lines.append(f"Eingegangen am:   {cancelled['received_at']}")
        if cancelled.get("reason"):
            lines.append(f"Grund (Patient):  {cancelled['reason']}")
    # One flag for both, because the practice's question is the same either way: does this one
    # need looking at for a possible Ausfallhonorar?
    short_notice = any(
        (summary.get(key) or {}).get("less_than_24_hours") for key in ("cancelled", "moved")
    )
    if short_notice:
        lines.append("Kurzfristig:      ja — weniger als 24 Stunden vorher")
    if callback := summary.get("callback"):
        lines.append(f"Rückruf an:       {callback['phone']} ({callback['urgency']})")
        if callback.get("callback_time"):
            lines.append(f"Wunschzeit:       {callback['callback_time']}")
        lines.append(f"Anliegen:         {callback['reason']}")
    if error := summary.get("technical_error"):
        lines.append(f"Technischer Fehler: {error}")

    lines.append(
        f"Aktion nötig:     {'ja' if summary.get('staff_action_required') else 'nein'}"
    )
    lines.append("")
    lines.append(f"Praxis:           {practice.name}, {practice.address}")
    lines.append(f"Call-ID:          {summary['call_id']}")
    if conversation_id is not None:
        # The handle for the stored transcript, not the transcript. It lives inside the protected
        # system for exactly the retention period and is deleted automatically (spec §7).
        lines.append(f"Gespräch:         {conversation_id.hex}")
    if summary.get("run_ids"):
        lines.append(f"Governed runs:    {', '.join(summary['run_ids'])}")
    lines.append(
        "Transkript und Audio werden nicht per E-Mail versendet. "
        f"Das Transkript wird nach {practice.policy.transcript_retention_days} Tagen "
        "automatisch gelöscht; Audio wird nicht gespeichert."
    )
    return "\n".join(lines)


async def send_call_summary(
    summary: dict[str, Any],
    *,
    language: str = "de",
    caller_number: str | None = None,
    conversation_id: uuid.UUID | None = None,
    practice: Practice | None = None,
    transport: mail.Transport | None = None,
) -> dict[str, Any]:
    """Send the summary to both practice mailboxes. Never raises.

    `transport` is the workspace's, resolved by the caller (`mail.transport_for`); without one the
    platform's is used.

    A mail server that is down must not take a phone call with it, and by the time this runs the
    caller has already hung up — so a failure is logged loudly and reported in the return value
    rather than propagated. The return value is what the conversation timeline records, which is
    how "the summary did not go out" stays a visible fact instead of a silent one.
    """
    configured_practice = practice is not None
    practice = practice or load_practice()
    # The practice's real mailboxes, unless an override says otherwise. A test call is
    # indistinguishable from a real one by the time it reaches here, so the only way to rehearse
    # without emailing the practice about a patient who does not exist is to redirect at the door.
    recipients = list(practice.recipients) if configured_practice else settings.summary_recipient_override or list(practice.recipients)
    subject = subject_for(summary)
    body = body_for(
        summary,
        language=language,
        caller_number=caller_number,
        conversation_id=conversation_id,
        practice=practice,
    )

    transport = transport or mail.platform_transport()
    if transport is None:
        # Supported, not broken: the practice can see exactly what would have been sent while the
        # mail processor's AVV/DPA is still being signed (spec §7).
        log.info(
            "call summary not sent (SMTP not configured) to=%s subject=%s\n%s",
            ", ".join(recipients),
            subject,
            body,
        )
        return {"sent": False, "reason": "smtp_not_configured", "recipients": recipients}

    # The practice's name and reply-to on the platform's own sending address: never the practice's
    # domain as the sender, which is what a mail server refuses (ADR-0024).
    identity = mail.Identity(
        name=practice.notify_name or practice.name,
        reply_to=practice.notify_reply_to or practice.email or None,
    )
    message = mail.compose(
        transport=transport, identity=identity, to=recipients, subject=subject, body=body
    )
    # A temporary failure gets two more attempts; a refusal is flagged at once (#141). Either
    # way the result says how many were made, and a failure lands in the History for staff.
    attempts = 0
    for delay in (0.0, *RETRY_DELAYS):
        if delay:
            await asyncio.sleep(delay)
        attempts += 1
        try:
            await asyncio.to_thread(mail.deliver, transport, message)
        except Exception as error:  # noqa: BLE001 — see docstring: a mail failure is not a call failure
            temporary = is_temporary(error)
            if temporary and attempts <= len(RETRY_DELAYS):
                log.warning(
                    "call summary attempt %d failed for call %s, retrying: %s",
                    attempts, summary.get("call_id"), error,
                )
                continue
            log.exception("call summary could not be sent for call %s", summary.get("call_id"))
            return {
                "sent": False,
                "reason": f"{type(error).__name__}: {error}",
                "recipients": recipients,
                "attempts": attempts,
                "temporary": temporary,
            }
        return {"sent": True, "recipients": recipients, "attempts": attempts}
    raise AssertionError("unreachable: the last attempt returns")
