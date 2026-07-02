# Drive SEO title regression

Date: 2026-07-02
Status: DONE

## Problem

After the viral Shorts SEO change, some Drive uploads started using titles that felt fabricated or overly templated, for example long "Epic ..." titles, generic "parecia uma cena comum" framing, forced "olha o detalhe", and thin titles like "Sonic perde".

This made new uploads look more AI-generated than the earlier successful shorts and likely hurt feed testing.

## Root cause

`backend/agents/ready_video_seo.py` was forcing a viral title shape even when the video analysis already had a natural specific hook. It also shortened titles with ellipsis and allowed generic fallback wording.

## Fix

- Keep natural specific hooks instead of forcing a viral template.
- Repair thin titles with the best available hook/summary/topic.
- Remove generic "parecia uma cena comum" and forced "olha o detalhe" wording.
- Strip artificial prefixes like "Epic".
- Shorten at word boundaries without ellipsis.
- Keep Drive descriptions cleaner and less spammy.

## Verification

- `python -m pytest backend\tests\test_ready_video_seo.py backend\tests\test_drive_library.py backend\tests\test_schedule_intelligence.py -q`
- Result: 23 passed.
