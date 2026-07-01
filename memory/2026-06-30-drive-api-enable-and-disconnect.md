# DEBUG REPORT - Drive sync 403 and disconnect control

- **Symptom:** Syncing a Drive folder from the Schedule page showed a raw `HttpError 403` saying Google Drive API had not been used or was disabled.
- **Root cause:** The OAuth client was connected, but `drive.googleapis.com` was not enabled in the same Google Cloud project (`gen-lang-client-0911592858`).
- **Fix:** Enabled Google Drive API in Google Cloud. Added a Drive disconnect action to the Schedule page, clarified that the Drive connection is global for the ready-video library, and converted noisy Google API exceptions into user-friendly messages.
- **Evidence:** `/drive-library/status` showed connected credentials, then `/drive-library/accounts/6/sync` succeeded after API activation with `imported: 646`, `seen: 646`.
- **Regression test:** `python -m compileall backend` and `npm run build` passed.
- **Related:** The connected Drive account must have access to the folder being indexed; otherwise the next failure mode will be a permissions error rather than API-disabled.
- **Status:** DONE
