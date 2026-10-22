# ADR-0037 — Letters as PDF: the kernel renders any workflow's letter, from its checked text

Status: accepted · 21 October 2026 · built (see Implementation status) · builds on ADR-0034
(letters) and ADR-0035 (approval gate)

## Context

Until now a tenant received a plain-text email with a `.txt` attachment. That is the part of the
product a third party actually holds in their hands, and the weakest. A Betriebskosten- or
Kautionsabrechnung is a letter: letterhead, recipient, date, subject, text, and the enclosure it
names.

## Decision

### D1 — The kernel renders the letter; modules only say who writes it

`app/letters/pdf.py` turns any workflow's `Draft` into a PDF, laid out after DIN 5008:
- the letterhead, then the sender line above the recipient;
- the date, the subject (the letter's title) and the text;
- the enclosure from page 2, fixed-width, its size fitted to the longest line, with longer lines
  wrapped at their indentation so columns stay columns and nothing is cut;
- a footer with the sender and the page.

`Draft` gains `sender` and `sender_address`. Each `immobilien` workflow fills them from a new
setting (`sender_address`, one line). No module draws anything.

### D2 — The PDF says exactly what the amount check approved

The renderer prints the draft's text and enclosure as they are. It computes and formats no
figure, so the letter-amount check of ADR-0034 still covers everything a tenant reads. A test
renders the letters of NK-02 and KA-02, extracts the text from the PDF itself, and runs the same
check on it.

### D3 — Dated the day it is sent, in Germany

The date is rendered, not stored: it is the day the letter goes out. It is taken in
Europe/Berlin, not on the server's clock (UTC on Railway), so a letter sent at half past midnight
does not carry yesterday's date. The approval's fingerprint (ADR-0035) covers the text and the
letterhead, not the date.

### D4 — Where it is used

- **Sending:** each email keeps the letter as its body and carries one PDF attachment (the letter
  and its enclosure) instead of the text file.
- **Studio:** each letter on a case has a PDF button
  (`GET /cases/{id}/letters/{n}/pdf`, a ready case only), showing what the tenant would receive
  if it were sent today.

### D5 — reportlab, a new dependency

The standard library cannot write PDF. reportlab (BSD) prints € „ “ – § ² with its built-in
fonts. fpdf2's built-in fonts refuse the € sign, and fixing that means bundling a font file.
Checked by generating both and reading the text back. Pinned `>=5,<6`.

## Not building

- A logo, colours, a second page header, or postal delivery.
- The recipient's postal address: the documents rarely state a former tenant's new address, and
  the letter goes by email. The block shows the recipient's name.
- PDF/A archiving: it may be needed for retention; decided with the retention policy.

## Implementation status (2026-10-21)

- `app/letters/pdf.py` (`letter_pdf`, `today`); `Draft.sender` / `sender_address`
  (`app/kernel/workflows.py`); `sender_address` in both `immobilien` settings; the PDF attachment
  in `app/approvals/service.py`; `GET /cases/{id}/letters/{index}/pdf`; the Studio's PDF button.
- Tests: `tests/test_letter_pdf.py`:
  - the letterhead, recipient, date, text and enclosure read back from the PDF;
  - every amount in the PDF is in the result, for both workflows;
  - German characters, and wrapping instead of cutting;
  - the endpoint: ready only, index bounds.

  `tests/test_approvals.py` now reads the mailed PDF.
- Checked in Chrome: the PDF button downloads Lindqvist's letter, two pages with letterhead and
  enclosure. Rendered pages inspected as images.
