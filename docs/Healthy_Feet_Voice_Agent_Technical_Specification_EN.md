# Healthy Feet München Voice Agent

## Functional Stack and Technical Specification

**Functional stack and technical specification for the developer**

**Version:** 1.1\
**Date:** 05.09.2026\
**Location:** Healthy Feet München\
**Status:** Ready for technical review

This document describes call scenarios, integration with Thevea, work
with patient records, the calendar, notifications, and security
requirements.

## 1. Objective

The voice agent handles incoming calls for Podologie Healthy Feet
München and must:

1.  Find actual available slots in Thevea.
2.  Book appointments for new and existing patients.
3.  Reschedule and cancel existing appointments.
4.  Answer basic questions about the Munich practice, services, prices,
    prescriptions, payment, address, and opening hours.
5.  Create a staff callback request if it cannot resolve an issue safely
    and accurately.
6.  After every call, send a summary to:
    -   annettedemko@gmail.com
    -   muenchen@healthyfeet-podologie.de

The agent supports German, Russian, and English, automatically detects
the patient's language, and continues the conversation in that language.
The conversation language is not stored in the patient's Thevea record.

The agent does not diagnose, prescribe treatment, promise insurance
coverage, or invent available slots.

## 2. Recommended Architecture

The stack should consist of five layers rather than a single prompt:

1.  **Telephony and voice** --- incoming phone number, speech
    recognition and synthesis for German, Russian, and English,
    automatic language detection and switching, interruption handling,
    and re-prompting for unclear data.
2.  **Dialogue orchestrator** --- an LLM with system rules and access
    only to approved functions.
3.  **Thevea Adapter** --- a separate server-side module that works with
    patients, calendars, and appointment statuses through Thevea's
    official API/partner interface.
4.  **Notifications** --- sending a summary email after every completed
    call.
5.  **Audit and security** --- function-call logging, idempotency keys,
    error handling, access restrictions, and minimization of medical
    data.

Direct control of the Thevea website through browser clicks must not be
used as a permanent solution: it is unreliable, difficult to test, and
risky for medical data.

## 3. Required Backend Functions

### 3.1 `search_availability`

**Purpose:** Find only genuinely available Thevea slots.

**Input:** - `location_id` --- always München - `resource_names` ---
strictly MA1, MA2, MA3 - `service_id` and `duration_minutes=30` -
`date_from`, `date_to` - `preferred_days[]` --- optional - `time_window`
--- `morning | midday | afternoon | evening | exact_range | any` -
`exact_time_from`, `exact_time_to` --- optional - `therapist_id` ---
only if the patient requests a specific specialist - `limit` --- default
3

**Output:** - `slot_id` - date, start time, and end time -
employee/resource - expiration time of the slot offer

**Rules:** - The agent never states a time that is not returned by the
function. - Search is performed only in the MA1, MA2, and MA3 calendars;
all other calendars and employees are ignored. - All three calendars are
equivalent: any service may be scheduled in any of them. - Every
appointment created by the agent is always 30 minutes long. - The
12:00--13:00 break is blocked and never offered. - If the patient does
not specify a time, search for the nearest available options. -
"Morning" should be a configurable interval, e.g. 09:00--12:00;
"afternoon" 13:00--16:00; "evening" 16:00--18:00. - These intervals must
be stored in configuration, not in the prompt. - The slot is checked
again immediately before final appointment creation.

### 3.2 `find_patient`

**Input:** - first name - last name - date of birth - phone number in
normalized E.164 format

**Matching logic:** - Exact match: first name + last name + date of
birth. - Phone number is used as an additional check. - Account for
case, spaces, hyphens, umlauts, and transliteration variants. - Never
automatically select a patient based only on first and last name. - If
multiple matches exist or data conflicts, do not disclose information
from patient records; create a staff request instead. - Do not create a
duplicate until the search has been completed.

**Output:** `none | unique_match | ambiguous`; return `patient_id` only
for a unique match.

### 3.3 `create_patient`

**Required fields:** - first name - last name - date of birth - phone
number - source: `voice_agent` - consent/legal basis for processing
according to the approved policy

The patient's address, email, and conversation language are neither
requested nor added to the patient record.

The record is created only immediately before a confirmed appointment.
If the patient ends the call before choosing a time, do not create an
empty record.

### 3.4 `get_patient_appointments`

Used for rescheduling and cancellation.

Before disclosing appointment information, the agent must verify
identity using at least: - first and last name - date of birth - date
and time of the specific existing appointment

The call may come from a number different from the one in the patient
record. This alone does not prevent rescheduling or cancellation if the
other verification data matches. The caller's number is stored only in
the technical call information.

If verification fails, the agent must not disclose the date, time,
treatment type, or existence of other appointments and must offer a
staff callback.

### 3.5 `create_appointment`

**Input:** - `patient_id` - `slot_id` - `service_id` -
`source=voice_agent` - short internal note without unnecessary medical
details - `call_id` as idempotency key

**Execution conditions:** 1. The patient has explicitly confirmed aloud
the name, date, time, and location. 2. The slot has been checked again
and is still available. 3. Exactly one patient record has been found or
created.

**Output:** `appointment_id`, confirmed date/time/service, or a
structured error.

### 3.6 `reschedule_appointment`

**Input:** `appointment_id`, `new_slot_id`, `call_id`.

**Requirements:** - Find the new slot first. - Obtain explicit patient
confirmation. - Perform the rescheduling atomically so the old
appointment is not lost if the new slot has already been taken. - Store
the old and new date/time in the audit log. - Do not create a second
appointment instead of rescheduling if Thevea's business logic supports
modification of the existing appointment.

### 3.7 `cancel_appointment`

**Input:** - `appointment_id` - Thevea status `abgesagt` or its exact
system identifier - time the cancellation was received - reason --- only
if the patient voluntarily provides one -
`less_than_24_hours=true|false` - `call_id`

Before cancellation, the agent always offers rescheduling once. If the
patient declines, cancel without pressure.

For a cancellation or rescheduling less than 24 hours before the
appointment, the agent says:

> Da Sie den Termin sehr kurzfristig absagen, kann ein Ausfallhonorar
> entstehen, falls der Termin nicht anderweitig vergeben werden kann.
> Die Praxis prüft das im Einzelfall.

The agent must not say that a fee or invoice is automatically issued or
"required by law."

### 3.8 `create_callback_request`

**Fields:** - first and last name, if known - confirmed phone number -
convenient callback time, if provided by the patient - language - brief
reason without independent medical interpretation - urgency:
`normal | urgent_review` - `call_id`

### 3.9 `send_call_summary`

Called after every accepted call without exception: after appointment
actions, callback requests, information-only calls, incomplete
conversations, and technical errors.

**Recipients:** - annettedemko@gmail.com -
muenchen@healthyfeet-podologie.de

**Email subject:**

`[Voice Agent] {ACTION} — {patient_name_or_unknown} — {call_date_time}`

Possible `ACTION` values: - `NEUER TERMIN` - `TERMIN VERSCHOBEN` -
`TERMIN ABGESAGT` - `RÜCKRUF ERBETEN` - `NUR AUSKUNFT` -
`NICHT ABGESCHLOSSEN` - `TECHNISCHER FEHLER`

**Content:** - call date and time - caller's phone number - language -
outcome - patient name, if obtained - created/modified appointment - old
and new dates when rescheduling - indication of short-notice
cancellation - whether staff action is required - technical call
identifier - link or internal identifier for the stored transcript, if
supported by the interface

The full transcript and audio are not sent by email. Do not send the
full date of birth or detailed description of a medical condition via
ordinary email. `call_id` is sufficient to locate the call; sensitive
details must remain in the protected system.

## 4. Conversation Scenarios

### 4.1 Start of Call

Recommended German phrase:

> Guten Tag, Sie sprechen mit dem digitalen Telefonassistenten der
> Podologie Healthy Feet München. Wie kann ich Ihnen helfen?

The agent must clearly state that it is a digital/AI assistant. It
automatically detects the language of the first meaningful utterance and
speaks German, Russian, or English. If the patient changes language
during the call, the agent switches automatically. Other languages are
not presented as supported.

Call audio is not stored. The text transcript is stored within the
protected system for 30 days and then automatically deleted. The patient
receives a brief notice about data processing according to the approved
privacy policy.

### 4.2 New Appointment

Procedure:

1.  Identify intent: the patient wants to book an appointment.
2.  Ask whether the patient has a Muster 13 prescription, a private
    prescription, or is self-paying. This affects the service type but
    does not replace verification of the original prescription at the
    practice.
3.  Ask which procedure is desired. If the patient does not know, do not
    force a choice and do not diagnose; use **Medizinische Fußpflege**
    with a 30-minute duration, and the exact procedure will be clarified
    at the practice.
4.  Ask for preferences: nearest available appointment, specific
    week/day, time range, or morning/afternoon/evening.
5.  Call `search_availability` and offer no more than three nearest
    suitable options, one at a time.
6.  After a slot is selected, collect first name, last name, date of
    birth, and phone number. Do not ask again for information already
    reliably obtained during the conversation.
7.  Repeat critical data in parts. Confirm the phone number and date of
    birth separately.
8.  Call `find_patient`.
9.  For `unique_match`, use the existing record; for `none`, prepare to
    create a new one; for `ambiguous`, hand off to staff.
10. Recheck the slot.
11. Give a brief summary and request explicit confirmation of date,
    time, address, and appointment type.
12. After "yes," create the patient record if necessary and create the
    appointment.
13. Only after a successful response from Thevea state that the
    appointment has been booked.
14. Send the summary email.

A caller may book for another person, including a child or relative. The
agent clarifies that the appointment is for another person and collects
the patient's first name, last name, date of birth, and phone number.
Caller data does not replace patient data. For a child, the contact
number of a parent or legal guardian may be used.

If the selected slot is taken during the conversation, apologize, run
the search again, and offer new options. Do not promise that the
practice will manually add the appointment later.

### 4.3 Search Without an Exact Time

Examples the agent must understand: - "When is your next available
appointment?" - "Next week." - "Tuesday afternoon." - "Morning works
better for me." - "After 16:30, any day." - "Not before 15 September."

The agent converts these into a search range but does not invent an
exact date. If the request is too broad, it starts with the nearest
available slots. If it is too narrow and nothing is available, it offers
to broaden the range.

### 4.4 Rescheduling

1.  Verify identity.
2.  Find the specific future appointment.
3.  If there are multiple appointments, ask the patient to state the
    date themselves or clarify without revealing unnecessary
    information.
4.  Collect new preferences.
5.  Find an actual available slot.
6.  Repeat the old and new appointments and obtain confirmation.
7.  Execute `reschedule_appointment`.
8.  Confirm only after a successful Thevea response.
9.  Send an email containing both dates.

### 4.5 Cancellation

1.  Verify identity and the appointment.
2.  Offer rescheduling once:

> Möchten Sie den Termin lieber direkt verschieben, damit Sie keinen
> neuen Termin separat vereinbaren müssen?

3.  If the patient agrees, switch to the rescheduling flow.
4.  If the patient declines, determine whether fewer than 24 hours
    remain before the appointment.
5.  For cancellation or rescheduling less than 24 hours before the
    appointment, give the warning about a possible `Ausfallhonorar`.
6.  Ask for final confirmation of cancellation.
7.  Set the status to `abgesagt`, preserving the appointment and its
    history; do not permanently delete it.
8.  Send the email.

### 4.6 Staff Callback

Create a request if: - Thevea/API is unavailable - multiple patient
records are found - identity cannot be verified - the question is
medical or non-standard - the patient disputes an invoice, insurance
coverage, or `Ausfallhonorar` - urgent professional assessment is
required - the patient explicitly asks for a human

The agent confirms the phone number and does not promise an exact
callback time unless one has been defined by the practice.

## 5. München Knowledge Base

The source of truth for dynamic data must be a separate configuration
file or CMS with an update date. Prices and opening hours should not be
permanently hard-coded into the system prompt.

### Contact Details

-   **Address:** Baumkirchner Straße 19, 81673 München
-   **Phone:** 089 4111 5335
-   **Email:** muenchen@healthyfeet-podologie.de
-   **Opening hours:** Monday--Friday 09:00--12:00 and 13:00--18:00;
    Saturday and Sunday closed
-   **Payment:** cash or card
-   **Booking calendars:** MA1, MA2, MA3 only
-   **All appointments:** 30 minutes
-   **Break:** 12:00--13:00; no bookings possible

### Published Private Services and Prices

  ------------------------------------------------------------------------
  Service                                      Price Approximate Duration
  --------------------- ---------------------------- ---------------------
  Medizinische                                   €69 30 min
  Fußpflege / komplette                              
  podologische                                       
  Behandlung                                         

  Hornhaut entfernen                             €55 30 min

  Hühnerauge entfernen                           €55 30 min

  Warze abtragen                                 €55 30 min

  Eingewachsenen Nagel                           €79 30 min
  behandeln, ohne                                    
  Spange                                             

  Kinder-Nagelschnitt                            €55 30 min
  inkl. Elternberatung                               

  Erstberatung                                   €25 per configuration

  Kontrolltermin                                 €15 per configuration

  Nagelpilzbehandlung                            €55 30 min
  klassisch                                          

  Kaltplasma, Paket 6                           €375 30 min/session
  Sitzungen                                          

  Kaltplasma, Paket 12                          €669 30 min/session
  Sitzungen                                          

  Nagelspange, 1.                               €140 30 min
  Sitzung                                            

  Nagelspange,                                   €59 30 min
  Folgesitzung                                       

  Hausbesuch, one                                €95 30 min plus logistics
  person                                             

  Hausbesuch, two or                  €80 per person 30 min/person plus
  more people at one                                 logistics
  location                                           
  ------------------------------------------------------------------------

Before launch, all prices must be mapped to the actual `service_id`
values and durations in Thevea. The agent must not independently select
a package, type of brace, or number of procedures.

### Heilmittelverordnung Muster 13

-   The practice accepts patients with a valid **Heilmittelverordnung
    Muster 13** for podiatry.
-   The patient must bring the original prescription and insurance card
    to the first appointment.
-   Final verification of the prescription's validity and eligibility
    for reimbursement is performed at the practice; the agent does not
    guarantee insurance coverage.
-   For adult patients with statutory health insurance, the statutory
    co-payment usually applies: €10 per prescription plus 10% of the
    cost of the prescribed treatments, unless the patient is exempt from
    co-payments.
-   A patient with a valid `Befreiungsausweis` must bring proof of
    exemption.

Recommended phrase:

> Mit einer gültigen Heilmittelverordnung Muster 13 kann die Behandlung
> über die gesetzliche Krankenkasse abgerechnet werden. Erwachsene
> zahlen normalerweise die gesetzliche Zuzahlung von zehn Euro je
> Verordnung plus zehn Prozent der Behandlungskosten, sofern keine
> Zuzahlungsbefreiung vorliegt. Bitte bringen Sie das Originalrezept,
> Ihre Versichertenkarte und gegebenenfalls den Befreiungsnachweis mit.

### Private Prescription

Recommended phrase:

> Bei einem Privatrezept bezahlen Sie die Behandlung zunächst selbst in
> unserer Praxis. Ob und in welcher Höhe Ihre Versicherung die Kosten
> erstattet, klären Sie bitte direkt mit Ihrer Versicherung.

The agent does not promise reimbursement and does not assess the
patient's specific insurance tariff.

## 6. Agent System Rules

1.  Be polite, calm, and concise; ask one question at a time.
2.  Do not say that an appointment has been created, rescheduled, or
    cancelled until the corresponding function returns success.
3.  Never invent available slots, prices, insurance policies, or patient
    data.
4.  Before any booking, repeat the date and time in an unambiguous
    format, including the year when necessary.
5.  Verify identity before changing an existing appointment.
6.  Obtain explicit confirmation before every appointment-related
    action.
7.  Do not disclose patient information to third parties. Parents,
    guardians, and relatives require a separate approved authorization
    flow.
8.  Do not diagnose or provide medical guarantees.
9.  When in doubt, create a staff request rather than improvising.
10. Do not ask for the patient's address unless required for a specific
    process. For an ordinary appointment, first name, last name, date of
    birth, and phone number are sufficient.
11. Send a summary notification after every call.
12. In case of a technical error, clearly state that the action was not
    completed and create a callback request.
13. Do not store the conversation language, email, or address in the
    patient's Thevea record.
14. If the appointment is being made for another person, use the
    patient's data rather than the caller's.

## 7. Critical Technical Requirements

-   Official API or written authorization from Thevea for the
    integration.
-   Separate service account with minimum required privileges.
-   Encryption of data in transit and at rest; secrets only in a secret
    manager.
-   AVV/DPA with all processors that receive voice, transcripts, or
    patient data.
-   Preferably, storage and processing in the EU/EEA; international
    transfers must be handled separately from a legal perspective.
-   Do not store audio. Store text transcripts inside the system for
    exactly 30 days, then delete them automatically.
-   `call_id` and idempotency keys to protect against duplicate patient
    records and duplicate appointments.
-   Recheck the slot immediately before booking.
-   Complete audit log: which function was called, when, with which
    identifiers, and with what result; without unnecessary medical text.
-   Error monitoring and immediate email notification if an operation is
    only partially completed.
-   Thevea test environment or test patients before access to real data.

## 8. Acceptance Tests

Before launch, perform at least the following tests:

1.  New patient, nearest available time.
2.  New patient, specific day without a time.
3.  New patient, only "morning."
4.  Existing patient found without creating a duplicate.
5.  Two patients with the same first and last name.
6.  Incorrectly recognized date of birth followed by correction.
7.  Slot is taken between being offered and confirmed.
8.  Duplicate `create_appointment` call due to a network retry.
9.  Rescheduling one of several future appointments.
10. Cancellation less than 24 hours in advance with the correct warning.
11. Patient declines rescheduling and completes cancellation.
12. Thevea is unavailable while creating an appointment.
13. Patient asks for medical advice.
14. Patient asks for a human.
15. Call drops after obtaining the phone number.
16. Verify both summary emails and absence of unnecessary medical data.
17. German, Russian, and English calls with automatic language detection
    and switching.
18. Unclear speech, background noise, and spelling correction of the
    last name.
19. Slot search only in MA1, MA2, MA3, with no offers during
    12:00--13:00.
20. Booking for a child or relative using the patient's own data.
21. Rescheduling and cancellation from a different caller number after
    successful data verification.
22. Automatic transcript deletion after 30 days and confirmation that no
    audio is stored.

## 9. Sources for Verification

-   Healthy Feet --- prices:
    https://www.healthyfeet-podologie.de/de/preise
-   Healthy Feet --- services:
    https://www.healthyfeet-podologie.de/de/leistungen
-   Healthy Feet --- München:
    https://www.healthyfeet-podologie.de/de/standorte
-   Thevea --- finding available appointments:
    https://support.thevea.de/hc/de/articles/7405528194461-Freie-Termine-finden-mit-Hilfe-der-Funktion-Termine-planen
-   Thevea --- patients:
    https://support.thevea.de/hc/de/articles/31235139798813-FAQ-Patienten-in-thevea
-   BMG --- statutory co-payment for remedies:
    https://www.bundesgesundheitsministerium.de/heilmittel
-   Verbraucherzentrale --- missed appointment fees:
    https://www.verbraucherzentrale.de/wissen/gesundheit-pflege/aerztinnen-und-kliniken/gebuehr-fuer-verpassten-arzttermin-ist-das-zulaessig-13939
-   EU AI Act, Art. 50:
    https://eur-lex.europa.eu/eli/reg/2024/1689/oj/eng
-   § 201 StGB, confidentiality of the spoken word:
    https://www.gesetze-im-internet.de/stgb/\_\_201.html
