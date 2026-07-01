# Drive Monetization And Smart Schedule Design

## Goal

Ready-to-post Drive videos must receive the same revenue and language packaging as AI-generated videos, while the smart scheduler should pick safer, more explainable posting times.

## Scope

- Apply the configured monetization CTA to YouTube descriptions generated for Drive-ready videos.
- Apply configured YouTube title/description localizations to Drive-ready videos.
- Keep Drive publishing lightweight: no script, no editing, no full AI generation pipeline.
- Improve smart scheduling with stronger validation, safer learned-time buckets, and less surprising previews.
- Do not deploy automatically. Show the implementation review first and wait for approval.

## Design

### Runtime SEO Enrichment

Create a shared helper in the SEO module that applies runtime YouTube enrichment to an existing SEO package:

- Ensure `seo.youtube.description` starts with the configured CTA when monetization is enabled.
- Avoid duplicate CTA insertion on retries.
- Generate `seo.youtube.localizations` using the existing localization settings.
- Leave platform captions and Drive video analysis untouched.

The existing `SEOAgent` and the Drive-ready video builder both call the helper before storing/publishing SEO.

### Smart Scheduling

The scheduler keeps the current `fixed`, `smart`, and `trending_aware` modes. The improvement is intentionally conservative:

- Backend validates schedule mode, `videos_per_day` range, timezone, and `HH:MM` post times.
- Smart learning buckets analytics by the actual publish/scheduled time, not the analytics collection time.
- Learned hours are blended with the default best-time ranking so sparse data cannot hijack the cadence.
- Multiple daily posts prefer a minimum spread between hours where possible.
- Slot preview matches the scheduler's real slot projection.

### Testing

Add backend unit coverage for:

- Drive SEO receives monetization CTA without duplicating it.
- Drive runtime localization can be called safely when disabled/unavailable.
- Smart learning uses scheduled publish hour instead of the 2h collection hour.
- Invalid schedule payloads are rejected by validation.

## Rollout

After local tests and frontend build pass, present the review to the user. Deploy to Vercel/Railway only after explicit approval.
