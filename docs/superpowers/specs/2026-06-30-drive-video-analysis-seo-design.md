# Drive Video Analysis SEO Design

## Goal

Improve ready-to-post Google Drive uploads so the system does not publish generic metadata such as `Legendado (1)` or `Video pronto da biblioteca`. A Drive video should be packaged like a real channel upload: strong title, specific description, useful tags, platform captions, hashtags, and a clear score.

The feature must keep the current Drive publishing promise: ready videos are downloaded, queued, and uploaded without editing the media. Analysis improves metadata only.

## Chosen Approach

Use a two-layer pipeline:

1. Lightweight content analysis after the selected Drive video is downloaded.
2. Strong deterministic fallback when AI, vision, transcription, or quota is unavailable.

This gives better SEO when the system can inspect the video, but never blocks the schedule just because an AI provider is down.

## Runtime Flow

1. The scheduler reserves the next matching `ReadyVideo`.
2. The Drive service downloads only that selected file into the job temp folder.
3. A new ready-video analysis service probes the local file with `ffprobe`.
4. The service extracts a tiny set of visual signals when safe: duration, resolution, aspect ratio, audio presence, and up to three low-resolution frames.
5. If an LLM provider is available, the system uses the filename, folder path, niche, channel context, probe data, and optional frame descriptions to create a content summary and packaging brief.
6. A Drive SEO builder generates final `job.seo_metadata`.
7. If any analysis step fails or times out, the system falls back to deterministic Drive SEO.
8. The publisher uploads the original file using the existing publish flow.

Analysis must run after `DriveLibraryService.download_for_job(...)` and before `job.seo_metadata` is assigned in `_try_create_ready_video_job`.

## Analysis Contract

Input:

```json
{
  "job_id": 123,
  "ready_video_id": 456,
  "local_path": "tmp/ready_videos/job_123/video.mp4",
  "drive_name": "Woody policia parte 1.mp4",
  "folder_path": "VIDEOS MEMES / DESENHOS ANIMADOS / PRONTO PARA POSTAR",
  "niche": "DESENHOS ANIMADOS",
  "content_type": "film_recap_ai_images",
  "video_format": "short",
  "account_context": {
    "display_name": "ONDA444",
    "niche": "desenhos animados",
    "language": "pt-BR"
  }
}
```

Output:

```json
{
  "status": "ok",
  "analysis_source": "probe_llm",
  "duration_seconds": 9,
  "width": 1080,
  "height": 1920,
  "aspect_ratio": "9:16",
  "has_audio": true,
  "summary": "Cena curta de humor com personagem de desenho em uma situacao policial.",
  "topics": ["desenho animado", "humor", "nostalgia"],
  "entities": ["Woody"],
  "hook": "Essa cena do Woody nunca envelhece.",
  "warnings": []
}
```

The result is stored in `job.video_context.content_analysis` and summarized in `ready.metadata_json.analysis`.

## SEO Contract

The final metadata must keep the existing publisher shape:

```json
{
  "youtube": {
    "title": "Essa cena do Woody nunca envelhece #Shorts",
    "description": "Uma cena classica de desenho animado com humor nostalgico direto ao ponto.\n\nComenta se voce lembra desse momento.\n\n#Shorts #DesenhoAnimado #Humor",
    "tags": ["woody", "desenho animado", "humor nostalgico"],
    "category_id": "24",
    "thumbnail_text": "NUNCA ENVELHECE"
  },
  "tiktok": {
    "caption": "Essa cena do Woody nunca envelhece #desenho #humor #nostalgia #fyp"
  },
  "instagram": {
    "caption": "Uma cena curta de humor nostalgico para quem cresceu vendo desenho.",
    "hashtags": ["#reels", "#desenhoanimado", "#humor", "#nostalgia"]
  },
  "seo_score": {
    "value": 80,
    "verdict": "drive_video_strong"
  },
  "seo_notes": []
}
```

The description must not mention internal implementation details like Drive, library, automation, filename, or `Video pronto`.

## Deterministic Fallback

When analysis or LLM is unavailable, the fallback builder must:

1. Clean operational filename noise: extension, dates, ids, `final`, `edit`, `export`, `9x16`, `1080p`, `parte`.
2. Extract useful terms from file name, folder path, niche, and account niche.
3. Select title formulas by content type and format.
4. Generate a specific first-line promise.
5. Generate 8-18 tags, with the primary keyword first.
6. Generate YouTube hashtags separately from TikTok and Instagram hashtags.
7. Produce `seo_score` explaining whether it was analysis-based or fallback-based.

The fallback should be much better than the current generic SEO, but it must not invent specific facts not present in filename, folder path, or channel context.

## Reliability Rules

- Do not analyze every indexed Drive video during sync.
- Do not re-render or edit the ready video.
- Use hard timeouts for probing, frame extraction, and LLM packaging.
- If analysis fails, publish with fallback SEO instead of marking the job as failed.
- Preserve no-auto-retry markers for auth and quota failures so the scheduler does not burn IA on jobs that cannot publish.
- Keep `ReadyVideo` reservation semantics to prevent duplicates, but expose reserved/error inventory clearly in a separate UI pass.

## UI

For this iteration, the backend will store richer `seo_metadata` and `video_context.content_analysis`. The existing queue and approval screens can display the improved title/description through the current job payload.

A separate UI pass can add:

- Drive analysis status chips in Agenda.
- Average SEO score for indexed Drive inventory.
- A reserved/error inventory action to release stuck Drive videos.

## Tests

Backend tests should cover:

- Filename cleanup.
- Deterministic fallback SEO.
- Analysis failure falls back without failing the job.
- Generated YouTube description excludes internal Drive/library wording.
- Short videos include `#Shorts` appropriately.
- Ready video jobs still mark the selected Drive item as reserved and never repeat already used videos.

Manual production check:

1. Configure one channel as Drive only.
2. Sync a Drive folder.
3. Let one scheduled slot create a Drive job.
4. Confirm the job skips scriptwriter and gets content-specific SEO.
5. Confirm YouTube upload uses the improved title/description/tags.
