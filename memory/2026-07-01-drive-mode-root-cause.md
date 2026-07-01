# Drive mode root cause - 2026-07-01

## Symptom

Channels configured as `Drive` still produced AI jobs tagged as trending/"do momento". Those jobs failed in `scriptwriter` with unavailable grounding/LLM errors.

## Root cause

The normal schedule path respected `video_source_mode`, but the independent `_job_ride_trends` scheduler did not. If `ride_trends` was enabled, it created AI-generated trending jobs for the account even when the channel source was set to `drive`.

The Drive scheduler also still depended on pending `ThemeQueue` rows. In pure Drive mode, the schedule should be able to reserve the next ready video directly from `ready_videos` without requiring a theme.

## Fix

- `_job_ride_trends` now skips accounts whose `video_source_mode` is `drive`.
- `_job_consume_themes` can now create a Drive ready-video job directly when a Drive-only channel has due slots and available inventory, even with no pending themes.
- Drive ready-video jobs now use deterministic SEO metadata from filename/niche/folder path instead of invoking the LLM SEO path.
- `/drive-library/videos` lists videos in queue order.
- Schedule UI fetches more Drive videos and shows them in a scrollable ordered list.

## Verification

- `python -m compileall backend`
- `npm run build`
- Inline Python check for `_clean_ready_title` and `_ready_video_seo`
