# Channels / Agenda / Refresh Orchestration Layer — Overview

Retroactive overview for the layer added in commit `fc6effb` (`feat: add
channels/agenda/session/refresh orchestration layer`), on top of
`f9ede09`'s editorial curation layer. Each module already has a good
docstring for what it does in isolation; this doc is the one place that
explains how they fit together and, more importantly, **why this layer
exists side by side with the automatic `scheduler.py`** instead of
replacing it.

## Why this exists

Before this layer, the only way a channel's videos got scheduled was the
automatic `scheduler.py` tick (`_job_consume_themes` /
`_try_create_ready_video_job`), driven by `ThemeQueue` slots and running
unattended on a fixed cadence. That is still true today — this layer does
**not** touch or replace it.

What was missing was an *operator-driven* view and action on top of the same
underlying data: "what is channel X's plan for today, and can I make it
happen now instead of waiting for the next tick / can I re-run design on
videos already curated." `Channel` → `PublishSession` → `agenda.py` →
`channel_refresh.py` is that layer. It reuses the automatic scheduler's own
reservation function (`_try_create_ready_video_job`) rather than
reimplementing job creation, so there is exactly one code path that turns a
Drive-ready video into a `VideoJob`.

## The pieces, top to bottom

```
PlatformAccount (identity/OAuth/quota)
        |  1:1
        v
      Channel (backend/models/channel.py)
        - orchestration config: visual_theme, intro_mode, tts_voice,
          daily_limit_long/short, posting_window_start/end
        - niche/account fields read through to PlatformAccount, not copied
        |  1:N
        v
  PublishSession (backend/models/publish_session.py)
        - one row per (channel_id, date)
        - planned_items / published_items: lists of VideoJob ids
          (references only — VideoJob stays the source of truth)
        - status: pending -> running -> completed | partial_failure
        |  references
        v
      VideoJob (existing model, untouched schema-wise)
        - created via scheduler._try_create_ready_video_job, the SAME
          function the automatic ThemeQueue scheduler calls
        - job.publish_session_id links it back to the PublishSession
          that planned it (nullable — automatic-scheduler jobs have none)
```

Two more modules operate on that structure:

- **`backend/agents/agenda.py`** — `get_or_create_session` (read-only) and
  `generate_daily_agenda` (does real work: reserves Drive videos and creates
  `VideoJob` rows up to the channel's daily caps, spaced across its posting
  window). Idempotent: caps are checked against *every* `VideoJob` already
  scheduled that account/format/day, not just `planned_items`, so it can run
  alongside the automatic scheduler on the same account without either one
  blowing past the channel's daily limit.
- **`backend/agents/channel_refresh.py`** — `refresh_job` /
  `refresh_channel`. Idempotent re-curation: re-runs
  `ready_video_curation.py`'s overlay/intro pass only when the inputs that
  actually drive it (vision analysis, channel visual identity) changed since
  the last refresh, tracked via a `curation_fingerprint` hash stored on
  `job.video_context`. Always re-curates from the pristine Drive source, never
  from an already-curated `main_video_path`, so refreshes don't compound.

## Entry points

Both read/write paths are exposed twice, once for humans-via-HTTP and once
for humans-via-terminal, sharing the same agent functions:

- `backend/routers/channels.py` — `GET/POST /channels`, `PATCH
  /channels/{id}`, `GET /channels/{id}/agenda` (read-only), `POST
  /channels/{id}/agenda/generate` (kicks off `generate_daily_agenda` on a
  background thread — it does the same slow, synchronous
  download+curation+dispatch work as the automatic scheduler, so it must
  never block a request thread), `POST /channels/{id}/preview` (design-only,
  no video encode), `POST /channels/{id}/refresh` (single job runs inline;
  whole-channel batch runs on a background thread for the same reason as
  `/agenda/generate`).
- `backend/cli.py` — `python -m backend.cli agenda --channel N [--generate]`
  and `python -m backend.cli refresh --channel N [--job N]`, calling
  `agents/agenda.py` and `agents/channel_refresh.py` directly (synchronous;
  no background-thread indirection needed from a terminal).

## Relationship to `scheduler.py`

Deliberately **not** wired into the automatic APScheduler ticks. An operator
decides when a channel's day gets planned or re-curated (via the endpoint or
CLI); nothing in this layer runs on its own schedule. The two mechanisms
share:

- the same reservation function (`_try_create_ready_video_job`), so there is
  one code path for "turn an available Drive video into a VideoJob";
- the same cap accounting (`agenda._count_scheduled` counts every
  non-errored `VideoJob` for the account/format/day regardless of which
  mechanism created it);
- the same `VideoJob` rows afterwards — `sync_published_items` recomputes a
  `PublishSession`'s `published_items`/status from real `VideoJob.status`
  after a publish tick, without the publish pipeline itself needing to know
  `PublishSession` exists.

If this layer is ever wired into an automatic tick, the cap-sharing logic
above is what has to keep working — that's the guarantee that would need to
be re-verified.

## Non-goals

- Does not replace `ThemeQueue` or `ScheduleConfig` — those still drive the
  automatic path independently.
- Does not add a second dispatch/publish pipeline — `VideoJob` status
  transitions and actual publishing are unchanged; this layer only creates
  jobs (via the existing reservation function) and re-curates their video
  file before they publish.
- Does not persist any plan beyond `PublishSession.planned_items` — there is
  no separate "schedule template" concept; the session IS the plan.
