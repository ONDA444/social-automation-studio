# DEBUG REPORT - Drive subfolder indexing

- **Symptom:** Syncing a Drive niche could index only one branch such as `+350 Cortes Family Guy`, miss another branch such as `Atualizados Semanalmente`, or fail with "Configure a pasta do Drive neste canal" when the input contained a Drive search URL.
- **Root cause:** The sync path preferred a stale `drive_folder_id` over the latest folder URL, so changing the visible input could still sync an older folder. It also only followed real Drive folder MIME types and did not follow Google Drive shortcuts to folders/files. Search URLs like `/drive/search?q=...` do not contain a folder id, so the previous code could not resolve them.
- **Fix:** Sync now prefers the current folder URL over stored ids, clears stale ids when the folder URL changes, follows Drive folder/file shortcuts, accepts video files by extension when Drive returns a generic MIME type, and falls back to searching accessible Drive folders by the search query/niche when no folder id is present.
- **Evidence:** `python -m unittest backend.tests.test_drive_library backend.tests.test_ready_video_seo` passes. `npm run build` passes.
- **Regression test:** `backend/tests/test_drive_library.py` covers generic video MIME detection, Drive search query parsing, nested folder walking, and folder shortcut walking.
- **Related:** The frontend now displays folder paths in the stock list so operators can confirm which branch a ready video came from.
- **Status:** DONE
