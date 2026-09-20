# Studio customer readiness review — 6 September 2026

Verdict: useful engineering prototype, but the current Studio does not provide a complete customer self-service path from practice setup to a verified live voice agent. Improving visual polish alone will not resolve the missing configuration and misleading deployment behavior.

Scope: source review of the landing page, Studio catalog, authoring/configuration components, voice tester, Calls, Runs, and the backend paths for tenancy, connections, deployment, calendar resolution, telephony and notifications. No authenticated rendered UI was available through the browser inventory. This is a code-based flow review, not an observed customer usability study or production certification. No live Thevea booking or phone call was performed.

## Findings in priority order

### P1 — Connection binding does not establish tenant ownership

`backend/app/api/v1/instances.py:77` converts submitted connection IDs to UUIDs and passes them to `repo.create_instance`. That function inserts bindings without fetching the connection under the caller's tenant. `backend/app/db/repo.py:397` resolves a voice instance's connection without checking its tenant. The relational schema links connection IDs but does not enforce equality of the two tenant IDs.

An authenticated tenant that obtains another tenant's connection ID could bind it to its own voice instance and reach that calendar. This is a source-confirmed missing authorization check; no cross-tenant exploit was attempted against a live database. Resolve each connection using the caller's tenant before persisting; validate adapter/capability compatibility; assert ownership again at runtime. Add a two-tenant regression test.

### P1 — Practice facts and notification recipients are global

`backend/app/workloads/conversational/practice.py` defaults to a cached `knowledge/muenchen.yaml`. The voice surface and calendar resolver do not inject a tenant-specific practice. `notifications.py:143` uses the same practice's recipients, or a global environment override.

A second customer's agent can therefore use the first practice's facts, booking schedule and notification destination. With SMTP configured, the wrong practice could receive call summaries. Introduce a tenant-owned practice configuration and pass the same version through prompt, calendar, booking and notifications. Do not enable multiple customers on the shared practice defaults.

### P1 — Thevea voice setup cannot be completed through the public form/API

`frontend/src/app/studio/configure/[template]/page.tsx:499` sends only adapter and credentials. Voice resolution requires `connection.config.rooms`, a nested mapping from practice resource names to actual Thevea `mandantMitarbeiterId` values. `backend/app/schemas/connection.py:25` restricts all config values to strings, rejecting that mapping. Passing JSON as a string is also incompatible with `_rooms_from`, which calls `.items()`.

Reproduced directly with the installed Pydantic model: `config={"rooms":{"MA1":4711,"MA2":4712,"MA3":4713}}` fails with `config.rooms: Input should be a valid string`. These numbers are illustrative, not real room IDs.

Add a typed Thevea voice configuration, room discovery/selection or a verified mapping form, a configuration update endpoint, and a read-only readiness check. Require every configured bookable resource to map to an actual room. Authentication alone does not prove booking readiness.

### P1 — Test and live modes are not trustworthy

`frontend/src/app/studio/voice/page.tsx:106` promises a per-session sandbox. `backend/app/api/v1/conversational.py:82` actually resolves the selected instance's real calendar when one is bound. Starting the tester can therefore enable real calendar writes despite the displayed promise. Summary emails also lack a per-session test destination.

Conversely, deployment auto-binds simulated connections for missing roles, and inbound telephony accepts the resulting sandbox. A live caller can receive a booking that exists only in memory.

Make mode explicit and server-enforced. Rehearsal must use isolated data and test notification routing. Live testing must clearly name the actual practice/calendar before starting. Activation must refuse simulated calendars and incomplete mappings. Return the selected instance and actual calendar mode to the UI.

### P1 — “Update instance” creates duplicate active deployments and ambiguous routing

The configuration page's `deploy` handler always calls the create endpoint. `repo.create_instance` always inserts a deployed row. Phone numbers have neither an ownership validation flow nor a uniqueness constraint. `instance_for_phone_number` returns the first matching deployed instance without deterministic ordering.

Changing a phone agent's settings can leave the previous configuration answering calls. Allowing tenants to type arbitrary numbers also does not establish their right to receive those calls. Implement actual revision/update semantics, verify phone assignment, and enforce one active owner per number. Make cutover atomic and keep an explicit rollback path.

### P2 — The tester does not identify which deployment it tests

`StudioVoiceSessionRequest` accepts language only. `repo.studio_voice_instance` selects the first deployed instance of the latest published voice template or creates a simulated one. It does not select the instance the customer was just configuring.

Test from a specific agent detail page using a tenant-validated instance ID. Show practice, revision, calendar and mode in the session. “View saved call” should link to that call rather than the unselected Calls list.

### P2 — Navigation and configuration expose engineering concepts

The catalog leads with raw template identifiers, version, risk, agent class and step counts; its empty state asks customers to author a template. The configuration screen places condition expressions and tool names before practice setup. Voice deployment ends with a JSON case-fixture editor rather than a test call.

Lead with customer jobs: “Answer calls and book appointments” and “Import appointments.” Keep contract authoring and raw traces in an advanced technical area. Replace the JSON tester for voice with a guided call rehearsal and observable booking result.

### P2 — Operations are split between real calls and sample runs

Calls fetches real conversations and offers transcripts and outcome filtering. Runs assigns `SAMPLE_RUNS` directly, even though configuration links there as the destination of real executions. It is labeled sample data, but does not fulfil the operational promise.

Connect Runs to tenant-scoped real execution data. Give staff a queue of callbacks, failed bookings and notification failures with ownership and completion status. Keep detailed tool traces behind each outcome.

### P2 — “Connected” does not expose verification or health

The connection response provides ID, adapter, type and creation date only. The credential probe treats timeout as acceptable and stores the connection, while the frontend displays it as connected. Destination preview is unavailable. Account choices also collapse to one newest connection per adapter, limiting multiple practice accounts in one organization.

Separate “credentials saved,” “access verified,” “calendars mapped” and “ready for booking.” Include last successful check and actionable reconnection status; name accounts by practice rather than by adapter alone.

### P2 — Real cancellation/rescheduling and audio acceptance remain incomplete

The Thevea calendar intentionally lacks `AppointmentLifecycle`; cancellation and rescheduling must become staff callbacks. Sandbox success does not establish those live capabilities. Existing mock tests also do not establish telephone audio quality, interruption recovery, notification delivery or exact live write verification. See the separate voice review for remaining verification and media-session findings.

## What the customer experience should be

Primary navigation: Overview · Voice agent · Calls & callbacks · Connections · Settings. Put calendar import under a clearly named workflow entry; put template authoring and runbook internals under Advanced.

The first screen should answer: Is my agent live? Which number does it answer? Which calendar does it use? What needs my attention? What must I do next?

Setup sequence:

1. **Practice:** name, address, timezone, opening hours, treatments, durations, prices, supported languages and callback destination.
2. **Connect Thevea:** authenticate, verify read access, select actual calendars and map allowed treatments/resources. Show capabilities explicitly: booking available; changes require staff.
3. **Agent behavior:** greeting, allowed bookings, required patient details and handoff rules using customer language.
4. **Rehearse:** isolated sample patients and appointments; selected agent revision; transcript and clear outcome.
5. **Verify a live test:** designated test account/data; show the exact appointment read back from Thevea and verify staff notification delivery.
6. **Activate phone:** verified number, connection health and readiness checks; explicit activation; pause/resume and revision history on the same page.

## How the existing integration is intended to connect

```text
Twilio number
  → POST /api/v1/telephony/incoming
  → deployed instance for that number
  → instance connection role: calendar
  → tenant's encrypted Thevea connection + verified room mapping
  → TheveaPracticeCalendar
  → Pipecat dialogue proposes booking
  → governed checks → appointment write → independent calendar read-back
  → saved call outcome + staff notification
```

The repository implements Thevea access through its web application's GraphQL/session endpoints. This review has not established a current official partner API agreement or live endpoint compatibility.

The exact mapping expected by the current resolver is `config.rooms = {"MA1": <actual-id>, "MA2": <actual-id>, "MA3": <actual-id>}` for the current knowledge file. The schema/form blocker must be fixed before this can be configured through the supported API. Do not guess IDs or treat direct database editing as a customer onboarding flow. Also align the connector's verification search rooms with all mapped voice rooms.

After the blockers are fixed, bind that connection to `voice_appointment` under the `calendar` role, configure the practice and notification destination, and deploy one instance with the verified number in E.164 format. Backend prerequisites include Clerk/tenant setup, database, `CONNECTION_ENC_KEY`, Deepgram/OpenAI/ElevenLabs credentials and voice ID, Twilio credentials, and SMTP if email delivery is expected.

Configure the Twilio number's incoming voice webhook as HTTP POST to `https://<public-backend>/api/v1/telephony/incoming`. The backend returns the media-stream connection to `/api/v1/telephony/media`; it needs public secure WebSocket reachability. Typing a number into Studio does not configure Twilio or forward an existing practice line. Twilio documents [incoming voice webhooks](https://www.twilio.com/docs/usage/webhooks/voice-webhooks) and [secure media streams](https://www.twilio.com/docs/voice/twiml/stream).

Do a designated live acceptance test before activation: exact patient/time/room read-back, occupied slot refusal, duplicate request, provider failure, dropped call during a write, and staff callback for unsupported changes. Do not interpret a successful login or mock test as this proof.

## Delivery order and validation

1. Close tenant ownership, shared practice/recipient, phone ownership and test/live isolation gaps.
2. Complete the Thevea configuration contract and readiness checks.
3. Build the guided customer setup and agent detail screen with real update/test/activate actions.
4. Wire the operational queue and real execution views, then run customer task testing on desktop and mobile.

Validation performed: frontend `npm run typecheck` passed. Focused backend suite (`test_studio_flow`, `test_thevea_practice_calendar`, `test_telephony`, `test_voice_booking`) returned **107 passed, 1 skipped**, with three dependency deprecation warnings. The DB-dependent Studio flow test skipped; the suite does not prove live database integration. The room-mapping schema failure was reproduced separately. No product implementation changes were made in this review.
