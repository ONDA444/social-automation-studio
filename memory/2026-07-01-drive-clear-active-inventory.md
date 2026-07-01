# Drive clear active inventory

## Symptom

After switching a channel from Family Guy to `CARROS E CAMINHÕES`, the Drive inventory UI still listed Family Guy rows marked as `missing`. The user also needed a manual "clear stock" action to reset a channel before syncing again.

## Root cause

The backend correctly marked old rows as `missing`, but the schedule UI loaded all statuses for the account. There was also no endpoint to clear active inventory for a single channel.

## Fix

- Schedule Drive list now requests only `status=available`.
- Added `POST /drive-library/accounts/{account_id}/clear`.
- Clear operation marks non-used account rows as `missing`, resets reservation fields, and preserves `used` rows as history.
- Added a "Limpar estoque deste canal" button.

## Evidence

`python -m unittest backend.tests.test_drive_library backend.tests.test_ready_video_seo`

Result: 16 tests OK.

`npm run build` in `frontend`

Result: Vite build OK.

## Status

DONE
