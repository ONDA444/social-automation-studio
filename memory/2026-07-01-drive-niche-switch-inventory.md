# Drive inventory when switching channel niche

## Symptom

When a channel switched from one Drive niche/folder to another, the schedule UI showed the old niche inventory mixed with the new one. Example: switching to `CARROS E CAMINHÕES` still listed `Cortes Family Guy` videos and inflated the indexed stock count.

## Root cause

The sync flow imported/updated new `ReadyVideo` rows but did not retire old account-specific rows that were no longer part of the current sync. The frontend also loaded `/drive-library/videos?account_id=...` without passing the current niche, so it displayed every video tied to the account.

## Fix

- After account sync, mark stale non-used videos for that account as `missing` when their Drive file ID was not seen in the current sync.
- Keep `used` rows untouched for history.
- Filter the schedule Drive inventory request by the current channel Drive niche.
- Show how many old videos were removed from active stock in the sync alert.

## Evidence

`python -m unittest backend.tests.test_drive_library backend.tests.test_ready_video_seo`

Result: 15 tests OK.

`npm run build` in `frontend`

Result: Vite build OK.

## Status

DONE
