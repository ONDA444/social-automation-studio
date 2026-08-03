# Drive Ready Videos Design

## Goal

Add a second content source beside the existing AI generator: ready-to-post videos stored in Google Drive folders. Each channel can choose AI only, Drive only, or Drive with AI fallback. The scheduler keeps deciding when to publish; the new source only changes where the next video comes from.

## Decisions

- Use Google Drive as the remote library. The server indexes folder metadata and downloads only the selected video for a job.
- Store a lightweight `ready_videos` inventory in the database with Drive ids, folder/niche, status, usage history, and optional account binding.
- Keep Drive credentials separate from YouTube/TikTok/Instagram channel credentials.
- Integrate at the scheduler layer. When a theme slot is due, the scheduler tries to reserve a matching ready video for that channel before creating a generated AI job.
- Reuse the existing publisher. A ready-video job receives `main_video_path`, `seo_metadata`, `video_context.source = "drive_ready_video"`, and normal schedule/publish status.

## Channel Modes

- `ai`: current behavior.
- `drive`: only use Drive videos. If no matching video exists, leave the theme pending and report the inventory gap.
- `mixed`: try Drive first; if no matching video exists, fall back to AI generation.

## Runtime Flow

1. Admin connects Drive once and registers one or more library folders.
2. The system indexes folder children recursively and records video files as `available`.
3. On each scheduled slot, the scheduler reads the channel mode and niche.
4. It reserves the next compatible `ready_videos` row atomically.
5. It creates a `VideoJob` using the ready video as source.
6. The service downloads the file into `tmp/ready_videos/job_<id>/`.
7. SEO metadata is generated from the channel, theme, filename, and niche.
8. Publishing follows the existing approval/auto-publish behavior.
9. After successful publish, the inventory row is marked `used`.

## UI

- Add Drive source controls inside Agenda for the selected channel.
- Show mode, folder URL/id, niche override, recursive indexing, and inventory stats.
- Show origin badges in queue rows so generated jobs and Drive jobs are easy to distinguish.
- Keep the channel card lightweight.

## Risks

- Drive credentials are required for private folders.
- Large videos still need temporary disk during upload.
- Long ready videos cannot publish to TikTok/Instagram unless a short file is selected or a later trimming step is added.
- The system must prevent duplicate publication through reservation status and Drive file id dedupe.
