# ADR-0026 — Recognising a treatment from the caller's words, ambiguity as data

Status: accepted · 19 October 2026 · implements #131 (Mia specification v3, item 4) · builds on
ADR-0025 (workspace service catalogue), ADR-0022 (one kernel, workspaces as data)

## Context

Callers do not say "Podologische Komplexbehandlung Groß". They say "Fußpflege", "педикюр", "a
yellow toenail", "die Spange nachstellen". The specification asks Mia to understand that in
German, Russian and English, from a dictionary the practice can edit without a developer, and to
ask — never choose — when the words fit several services: small vs full foot care, first vs
follow-up brace session, 6 vs 12 cold-plasma sessions, and a yellow nail, which must not book the
fungal treatment on its own.

ADR-0025 already stores per service how callers say it, per language, and puts it in the prompt.
A prompt list alone leaves the decision to the model's reading of it.

## Decision

### D1 — Ambiguity is the practice's data, not a separate concept

There is no "ambiguity group" entity. A phrase the practice enters on several services matches all
of them: "Fußpflege" on both foot-care sizes, "Nagelspange" on both brace sessions, "gelber Nagel"
on the fungal treatment and the consultation. A more specific phrase beats one it contains: "große
Fußpflege" is the full treatment alone. Any workspace — a practice, an estate agent — expresses
its own ambiguities the same way.

### D2 — One kernel function, used by calls and the Studio

`recognition.candidates(services, said)` is a pure function of the catalogue and the words: no
case or accents, long words matched by stem ("Warzen", "бородавку"), phrases inside compounds
("Dornwarze" contains "Warze"), short words only as themselves. No model, no network.

### D3 — A tool returns candidates and what to do; it decides nothing

`identify_service(said)` returns the matching services and an `agent_notes` line: one match → check
with the caller; several → ask which, naming them, and record nothing yet; an unlinked match → a
callback; none → ask what it is for, then the consultation or a callback. It is offered only to an
agent with a treatment list. The booking tool still accepts only linked, switched-on services
(ADR-0025 D3) — recognition is a signal, the gate stays where it was.

### D4 — The practice can try it

Studio → Knowledge base → Services has "Try what a caller might say", which runs the same function
on the catalogue as it is now.

## Consequences

- Healthy Feet enters the specification's dictionary as phrases on its services; shared phrases
  are what make Mia ask.
- The model is told to call the tool and to ask on several matches, but nothing in the backend yet
  refuses a `service_key` the caller never confirmed. Making the confirmed service a hard
  precondition of the booking contract is #142.
- Stemming is crude by design (prefix of the word less two letters). Irregular forms ("ноготь" /
  "ногти") need their own phrase; the "try" box shows a practice when one is missing.
- Bundled eval agent (`studio_agent.json`) got calendar ids — without them ADR-0025 made none of its
  treatments bookable — plus phrases, and three scenarios cover the yellow nail (de, ru) and the
  brace.
