# YouTube refresh token churn

Date: 2026-07-02
Status: DONE

## Symptom

Several queued YouTube uploads failed with `invalid_grant` and the UI asked to reconnect channels repeatedly.

## Root cause

The YouTube OAuth start URL always used `prompt=consent`. That is useful when a channel has no valid refresh token, but it is harmful for normal reconnect/open-login flows because Google can issue another refresh token for the same user/client grant. With multiple channels and repeated reconnects, this churn increases the chance that older stored refresh tokens become invalid.

The credential save path also overwrote existing credentials directly, so if Google returned an access token without a new refresh token, the stored long-lived token could be lost.

## Fix

- Only force Google consent for YouTube when the account is disconnected, in auth error, or has no valid refresh token.
- Preserve the existing YouTube refresh token when a successful OAuth callback does not return a new one.
- Keep forced consent available for true recovery from `invalid_grant`.

## Verification

- `python -m pytest backend\tests\test_youtube_oauth.py -q`
- `python -m pytest backend\tests\test_youtube_oauth.py backend\tests\test_ready_video_seo.py backend\tests\test_drive_library.py -q`
- Result: 23 passed.

## Operational note

Channels that already have an invalid/revoked refresh token still need one manual reconnect. After reconnect, the system should stop rotating tokens unnecessarily.
