# DEBUG REPORT - Drive OAuth redirect_uri_mismatch

- **Symptom:** Google blocked Drive login with `Erro 400: redirect_uri_mismatch`.
- **Root cause:** Production backend sends `https://backend-production-d314e.up.railway.app/drive-library/auth/callback`, but that URI still needs to be registered in the Google Cloud OAuth Client's authorized redirect URIs.
- **Fix:** Exposed the active Drive OAuth callback in `/drive-library/status` and `/drive-library/auth/start`, and added a copyable callback helper in the Schedule Drive panel.
- **Evidence:** Railway health is green. `/drive-library/status` returns the production callback. `/drive-library/auth/start` sends the same callback inside Google's OAuth URL. Vercel deployed the new frontend bundle successfully.
- **Regression test:** `python -m compileall backend` and `npm run build` passed.
- **Related:** This is a configuration drift issue between Railway/Vercel production URLs and Google Cloud OAuth configuration.
- **Status:** DONE_WITH_CONCERNS - code and deploy are fixed; Google Cloud still needs the authorized redirect URI added by an account with access to the OAuth project.
