# ADR-0029 — The workspace file store: bytes decide the type, the row is the truth

Status: accepted · 19 October 2026 · built (see Implementation status) · step 2 of ADR-0028's
build order · kernel service, used by every module

## Context

A document workflow (ADR-0028 D5) starts from files: a Hausgeldabrechnung, a Grundsteuer notice,
a heating bill. Until now the platform kept no files at all. Knowledge uploads (ADR-0017) arrive
as base64 in JSON and only their extracted text is saved. A settlement has to be traceable to the
document it was computed from, so the document itself must be kept, unchanged, and readable only
by its workspace.

Wonderful's file layer (read 2026-10-19) puts one blob interface over several clouds, treats a
file row as a reservation until the bytes are confirmed, and scans uploads in a separate adapter.
Its generic file store has no scan and no retention.

## Decision

### D1 — The bytes decide what a file is

The type is sniffed from the content: PDF (`%PDF-` within the first KiB), PNG, JPEG, or UTF-8 text
(`.csv` named as CSV). Anything else is refused with 415 before it is stored; the name and the
browser's `Content-Type` are ignored. The allowlist is what a workflow reads today; a new kind
(XLSX, DOCX) is added when a workflow can read it. 15 MB per file, enforced while the body
streams (413), so an oversized upload is never held in full.

### D2 — The row is the truth; the blob is addressed by a key the platform makes up

`stored_file` (tenant, display name, sniffed media type, size, SHA-256, storage key) is what the
platform knows about a file. Its bytes live in a `BlobStore` under `<tenant>/<file id>`; a user's
file name is a display name only (path and control characters stripped) and never part of a key.

`BlobStore` is three methods (`put`, `get`, `delete`). Two implementations:

- `SupabaseBlobStore`: a **private** Supabase Storage bucket (`workspace-files`, created on first
  use), over its REST API with the service-role key and the `httpx` already in use. Every read goes
  through the backend, which checks the workspace first.
- `LocalBlobStore`: a directory, for development and tests. **Production refuses it** (503): a
  container's disk does not survive a deploy, and a file that silently vanishes is worse than an
  upload that is refused.

### D3 — Uploads go through the backend, as the raw body

`POST /files?name=…` with the file as the body: no form encoding, so no new dependency. The
backend sniffs and hashes the bytes on the way through, which a presigned direct upload would
force it to download again to do. Direct-to-storage upload is worth it for large files or many
of them; neither is the case for a handful of PDFs per settlement.

### D4 — One workspace stores the same content once

Unique on `(tenant, sha256)`. Uploading a file again returns the stored one (200 instead of 201).
Two workspaces uploading the same bytes get two files: nothing is shared across workspaces, not
even storage. A concurrent duplicate upload that loses the race removes its blob and returns the
winner.

### D5 — Order of writes, and how a file leaves

Upload writes the blob before the row, so a row never points at nothing. Delete removes the blob
before the row, so a failed delete leaves a row the workspace can retry rather than an invisible
blob. A download is always `Content-Disposition: attachment` with `X-Content-Type-Options:
nosniff` and `Cache-Control: private, no-store`: a stored file is never rendered as a page of the
API's origin.

## Not building

- **Virus scanning.** Today only the workspace's own staff upload. It becomes necessary before
  files arrive from outside the workspace (an inbox, a tenant portal); the shape is Wonderful's:
  a separate scanner that reports back with a signed verdict.
- **Presigned direct uploads** (D3).
- **A retention sweep.** How long a document is kept is the customer's legal question
  (Aufbewahrungspflichten against data minimisation), and it differs by workflow; it is decided
  with the case model (ADR-0028 step 3), not guessed here.
- **OCR and text extraction** of stored files: the extraction step of the workflow does that.

## Consequences

- Production needs `FILES_BACKEND=supabase` and `SUPABASE_SERVICE_ROLE_KEY` on the backend
  service; until both are set, uploads answer 503 and nothing is lost.
- A file is deletable while nothing refers to it. Once cases refer to files (step 3), deleting a
  file a case was computed from must be refused, or the settlement loses its evidence.

## Open questions

1. Retention per workflow, and whether a workspace may shorten it.
2. When virus scanning becomes mandatory, and which scanner (ClamAV in a sidecar, or a service).
3. Whether knowledge uploads (ADR-0017) should keep their original through this store.

## Implementation status (2026-10-19)

- `app/files/content.py` (`sniff`, `safe_name`, `MAX_FILE_BYTES`), `app/files/store.py`
  (`BlobStore`, `LocalBlobStore`, `SupabaseBlobStore`, `blob_store`, `FileStoreUnavailable`),
  `app/files/service.py` (`store_file`, `remove_file`), `StoredFile` in `app/db/models.py`,
  migration `9c0d1e2f3a4b`, settings `FILES_BACKEND` / `FILES_DIR` / `FILES_BUCKET` /
  `SUPABASE_SERVICE_ROLE_KEY`.
- `GET`/`POST /files`, `GET /files/{id}`, `GET /files/{id}/content`, `DELETE /files/{id}`
  (`app/api/v1/files.py`).
- Studio: `app/studio/files/page.tsx` (drop or choose, per-file outcome, download, delete), rail
  entry "Files".
- `tests/test_file_store.py`: sniffing, names, both stores (Supabase against a mock transport),
  production refusal, and the API against Postgres (dedupe, refusals, attachment headers,
  isolation between workspaces, delete removes the bytes).
