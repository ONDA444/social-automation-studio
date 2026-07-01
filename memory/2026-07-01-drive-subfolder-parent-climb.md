# Drive sync from child folder links

## Symptom

Production kept syncing `0 novo(s), 53 atualizado(s)` for `VIDEOS RELIGIOSOS` and `0 novo(s), 431 atualizado(s)` for `CORTES FAMILY GUY`, while the Google Drive UI showed those URLs were inside niche folders with sibling subfolders.

## Root cause

The saved Drive URL could point to a child folder such as `Videos` or `+ 350 Cortes Family Guy`. The sync resolver searched the pasted folder and its descendants, but never climbed through Drive `parents` to find the actual niche folder. As a result it indexed only the pasted child folder.

## Fix

`DriveLibraryService._resolve_niche_roots()` now fetches folder metadata including `parents` and checks the current folder plus ancestors before searching descendants. If an ancestor matches the configured niche, that ancestor becomes the indexing root.

## Evidence

Regression tests cover:

- `Videos` with parent `VÍDEOS RELIGIOSOS` indexes both `Videos` and sibling `Cortes séries`.
- `+ 350 Cortes Family Guy` with parent `CORTES FAMILY GUY` indexes both `Atualizados Semanalmente` and `+ 350 Cortes Family Guy`.

`python -m unittest backend.tests.test_drive_library backend.tests.test_ready_video_seo`

Result: 14 tests OK.

## Status

DONE
