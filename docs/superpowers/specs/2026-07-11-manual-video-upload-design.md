# Manual Video Upload Design

## Goal

Let the user pick a video file from their own PC in the Agenda page and have it
published to a channel like any other job: analyzed (vision LLM), packaged
with a real title/description/tags, and sent to Approvals for a final human
check before going live. Available for any channel regardless of its source
mode (IA/Drive/Misto) — this is a standalone, one-off action, not a member of
the Drive rotation queue.

## Non-Goals

- Does **not** add the uploaded file to the `ReadyVideo` inventory/rotation
  pool. It is a single job, scheduled once, not reused or rotated.
- Does **not** change existing Drive/IA scheduling behavior.
- No video editing/transcoding — the uploaded file is published as-is, same
  promise as the existing Drive path.

## User Flow

1. User opens Agenda, selects a channel, clicks **"Enviar vídeo do PC"**
   (always visible, independent of the channel's `video_source_mode`).
2. A modal offers: file picker (`video/*`), an optional short text field
   ("Dica de contexto" — a theme/title hint), and a time choice: **Agora** or
   **Agendar para** (reveals a datetime picker).
3. On submit, the file uploads and the modal closes with a toast: "Vídeo
   recebido — analisando... vai aparecer em Aprovações em instantes."
4. The backend probes the file, extracts frames, asks the vision LLM to
   describe it, and packages title/description/tags/hashtags — the same
   engine already used for Drive ready-videos.
5. The job lands in **Aprovações** with `status=AWAITING_APPROVAL`. The user
   reviews/edits and approves — same approval flow as every other job.
6. Once approved, the existing publish pipeline (`_job_publish_due` /
   `run_publish`) picks it up like any other approved job: publishes
   immediately if the chosen time has passed, or as a scheduled YouTube
   upload (`publishAt`) if it's in the future.

## Architecture

Reuses `backend/agents/ready_video_seo.py` (`analyze_ready_video` +
`build_drive_seo` / `build_ready_video_package`) as-is — that module already
takes a duck-typed context (`drive_name`, `folder_path`, `niche`) rather than
a literal `ReadyVideo` row, so a manually uploaded file packages through the
exact same analysis engine used for Drive videos with zero duplication.

Deliberately **skips** the `ReadyVideo` table: no schema change, no risk of a
manual upload interacting with Drive's `reserve_next`/`mark_used` rotation
bookkeeping. The uploaded file's path is written straight onto the `VideoJob`
row (`main_video_path`), same field every job already uses.

### New backend module: `backend/agents/manual_upload.py`

- `run_analyze_upload(job_id: int) -> None` (async): loads the job, builds a
  duck-typed context object (`name`, `folder_path=None`, `niche=account.niche`),
  calls `ready_video_seo.build_ready_video_package(...)` with the hint as
  `title_seed`, sets `job.seo_metadata`, `job.video_context = {"source":
  "manual_upload", "hint": ...}`, `job.status = AWAITING_APPROVAL`,
  `job.approval_status = "pending"`. Wrapped in try/except: any failure (bad
  file, probe failure, LLM outage) still lands the job in AWAITING_APPROVAL
  using the deterministic fallback already built into
  `ready_video_seo` — analysis failure must never orphan the job in
  PROCESSING forever (same lesson from this session's stuck-publishing bugs).

### `backend/pipeline/dispatch.py`

- New `dispatch_analyze_upload(job_id: int) -> str`, mirroring
  `dispatch_job`/`dispatch_publish`. Runs under the existing `_render_sem`
  (CPU-bound: ffprobe + frame extraction), not `_publish_sem` — this is
  analysis work, not network publish.
- The ffprobe/frame-extraction calls in `ready_video_seo.py` already carry
  explicit subprocess timeouts (15s/18s) and the optional Gemini call already
  has an `httpx` timeout (45s) — no new unguarded blocking I/O is introduced,
  consistent with the timeout fixes already applied elsewhere this session.

### New endpoint: `backend/routers/schedule.py`

`POST /schedule/{account_id}/upload-video` (multipart/form-data):

- Fields: `file` (required), `scheduled_at` (optional ISO datetime; absent =
  now), `hint` (optional text, max ~200 chars).
- Validates: account exists; file extension in `{.mp4, .mov, .webm, .mkv}`;
  size under `settings.max_manual_upload_mb` (new config field, default 500).
  Reject oversized/bad-extension uploads with a 4xx **before** creating a job
  row — the user sees an immediate, specific error instead of a job that
  silently fails.
- Streams the upload to
  `tmp/manual_uploads/job_{id}/<sanitized_filename>` in chunks (no full-file
  buffering in memory — same OOM concern already documented in this repo for
  ffmpeg/render).
- Creates the `VideoJob` (status=PROCESSING, `account_id`, `target_platforms
  =["youtube"]`, `scheduled_at` from the field or `utcnow()`,
  `video_format` placeholder `"long"` — corrected after probe, mirroring how
  Drive jobs already do this), then calls `dispatch_analyze_upload(job.id)`
  and returns `{"job_id": ..., "status": "processing"}` immediately — the
  HTTP request does not block on analysis.
- If ffprobe can't read the file at all (corrupt/non-video), the job goes to
  `ERROR` with a clear message ("Arquivo de vídeo inválido ou corrompido.")
  instead of a generic packaging fallback — this is the one case where
  fallback SEO packaging would be actively misleading (there's no video).

### Frontend: `frontend/src/pages/Schedule.jsx`

- New button "Enviar vídeo do PC" in the Cadência panel, always rendered
  (not gated by `video_source_mode`).
- Small modal: file input, optional hint text input, "Agora" / "Agendar
  para" toggle (revealing a `datetime-local` input for the latter), Submit.
- On submit: `multipart/form-data` POST to the new endpoint. Show an
  indeterminate spinner during upload (real byte-progress is a nice-to-have,
  not required for v1 — keeps scope small). On success, toast + close modal.
  On validation error (400/413 from the backend), show the message inline in
  the modal instead of a generic alert.

## Config

`backend/config.py`: add `max_manual_upload_mb: int = 500`.

## Error Handling

| Failure | Behavior |
|---|---|
| File too large / bad extension | Rejected at the HTTP layer, 4xx, no job created. |
| ffprobe can't read the file | Job → `ERROR`, message "Arquivo de vídeo inválido ou corrompido." |
| Vision LLM / Gemini unavailable | Falls back to deterministic SEO packaging (existing `ready_video_seo` fallback path) — job still reaches Approvals. |
| Disk write fails mid-upload | Job → `ERROR` with the underlying message; partial file cleaned up. |

## Testing

New `backend/tests/test_manual_upload.py`:

- Endpoint rejects unsupported extensions and oversized uploads without
  creating a `VideoJob` row.
- `run_analyze_upload` on a valid sample video produces a job in
  `AWAITING_APPROVAL` with non-empty `seo_metadata.youtube.title`/`description`.
- `run_analyze_upload` on a corrupt file produces a job in `ERROR` with the
  expected message, not a silently-generic SEO package.
