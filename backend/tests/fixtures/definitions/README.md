# Definitions for the platform's own tests

The platform's tests run against this directory, never against `backend/definitions/`: that is
how they prove the platform works without the shipped templates (ADR-0039, success criterion 3).

- `demo/umlage`: a small shared-cost split, written to exercise every platform guarantee (questions
  block, a check refuses, letters are checked, approvals send). Plain dicts and hand-written JSON
  forms: a definition needs neither dataclasses nor a forms spec.
- `praxis`: a link to the real manifest. The practice's code still lives in the platform
  (voice, calendar sync), so its module is part of the platform until it moves out.
