# YouTube OAuth callback 500 on reconnect

## Symptom

The production callback URL `/auth/youtube/callback` returned `500 Internal Server Error` after Google login. Jobs stayed blocked with `invalid_grant`, asking the user to reconnect YouTube.

## Root Cause

The YouTube OAuth start URL uses `include_granted_scopes=true`. Because the same Google account had already granted Drive access, Google returned an extra `https://www.googleapis.com/auth/drive.readonly` scope in the YouTube callback token response.

`oauthlib` treats any returned scope set different from the requested YouTube scopes as a strict `Warning: Scope has changed...`, and `google_auth_oauthlib.flow.fetch_token()` raised it. The accounts callback did not catch that exception, so FastAPI returned 500 before credentials were saved.

## Fix

`backend/uploaders/youtube.py::exchange_code()` now temporarily sets `OAUTHLIB_RELAX_TOKEN_SCOPE=1` around `flow.fetch_token()`, then restores the previous environment value in a `finally` block. This accepts extra Google-granted scopes while keeping the rest of the OAuth exchange unchanged.

## Evidence

Regression test added in `backend/tests/test_youtube_oauth.py` simulates an exchange where Google returns YouTube scopes plus Drive readonly, confirms the relax flag is active during token exchange, confirms the env var is restored, and confirms the extra scope is preserved in saved credentials.

Validated with:

- `python -m pytest backend\tests\test_youtube_oauth.py -q`
- `python -m pytest backend\tests\test_ready_video_seo.py backend\tests\test_schedule_intelligence.py -q`

## Status

DONE
