"""
MusicCuratorAgent — pick a CC0 bed matching the EditingPlan mood/BPM, process it
to the target length, and emit beat timestamps for beat-sync.

Catalog: assets/music/music_catalog.json (see generate_music.py).
librosa is used for real beat detection when available; otherwise beats are
estimated from the catalog BPM.
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
import re
from pathlib import Path

from backend.agents.base_agent import BaseAgent
from backend.config import settings

logger = logging.getLogger(__name__)

MUSIC_DIR = settings.abs_path(settings.assets_dir) / "music"
CATALOG = MUSIC_DIR / "music_catalog.json"

# Catalog tracks have wildly different native loudness (measured up to ~13dB
# apart between tracks). editing_director.py then applies a FIXED volume_db
# offset per template on top of this un-normalised level, so the music ends up
# inconsistent between videos on the same channel depending only on which
# track got picked — sometimes near-inaudible, sometimes loud enough to
# compete with the narration. Normalise every processed bed to the same
# loudness so the fixed per-template offsets behave predictably.
_TARGET_DBFS = -20.0

# Map editing-plan / LLM moods to catalog moods (catalog tags: action, ambient,
# calm, dramatic, energetic, epic, lofi, melancholic, suspense, tense, upbeat,
# uplifting). Every catalog tag is also a key here so a direct request resolves to
# itself + neighbours; LLM-invented moods (curious/playful/cinematic…) map to the
# nearest beds so they never silently fall through to a wrong-mood global pick.
MOOD_ALIASES = {
    "dramatic": ["dramatic", "epic", "suspense"],
    "energetic": ["energetic", "action", "upbeat"],
    "upbeat": ["upbeat", "energetic", "uplifting"],
    "uplifting": ["uplifting", "upbeat", "calm"],
    "ambient": ["ambient", "calm", "lofi"],
    "calm": ["calm", "ambient", "lofi"],
    "suspense": ["suspense", "tense", "dramatic"],
    "tense": ["tense", "suspense", "dramatic"],
    "epic": ["epic", "dramatic", "action"],
    "action": ["action", "energetic", "epic"],
    "melancholic": ["melancholic", "ambient", "lofi"],
    "lofi": ["lofi", "ambient", "calm"],
    # Not catalog tags — nearest-bed mappings.
    "curious": ["ambient", "calm", "uplifting"],
    "playful": ["upbeat", "uplifting", "energetic"],
    "happy": ["uplifting", "upbeat"],
    "sad": ["melancholic", "ambient"],
    "cinematic": ["epic", "dramatic"],
    "inspirational": ["uplifting", "epic"],
    "relaxed": ["calm", "ambient", "lofi"],
}


class MusicCuratorAgent(BaseAgent):
    name = "music_curator"

    async def run(
        self,
        editing_plan: dict | None = None,
        narration: dict | None = None,
        **_,
    ) -> dict:
        editing_plan = editing_plan or self.ctx_get("editing_plan") or {}
        narration = narration or self.ctx_get("narration") or {}
        music_cfg = editing_plan.get("music", {}) or {}
        mood = music_cfg.get("mood", "dramatic")
        bpm_target = music_cfg.get("bpm_target", 95)
        target_len = float(narration.get("total_duration") or 60)

        catalog = self._load_catalog()
        if not catalog:
            self.emit("progress", "Catálogo de música vazio — pulando trilha", progress=70)
            self.ctx_set("music", {"processed_path": None})
            return {"processed_path": None}

        track = self._select(catalog, mood, bpm_target)
        src = MUSIC_DIR / track["file"]
        self.emit("progress", f"Música: {track['file']} ({track['mood']}, {track['bpm']}bpm)", progress=70)

        out_dir = self.job_dir(self.job_id, settings.abs_path(settings.output_dir))
        processed = out_dir / "music_processed.mp3"
        await asyncio.to_thread(self._process, src, processed, target_len)

        beats = self._beats(processed, track["bpm"], target_len)
        beat_path = out_dir / "beat_timestamps.json"
        beat_path.write_text(json.dumps(beats), encoding="utf-8")

        result = {
            "path": str(src),
            "processed_path": str(processed),
            "track": track,
            "beat_timestamps": beats,
            "beat_path": str(beat_path),
        }
        self.ctx_set("music", result)
        return result

    def _load_catalog(self) -> list[dict]:
        if CATALOG.exists():
            try:
                return json.loads(CATALOG.read_text(encoding="utf-8"))
            except Exception:
                return []
        return []

    @staticmethod
    def _mood_group(mood: str) -> list[str]:
        """Resolve a requested mood to a catalog-mood group, tolerating case and
        compound moods like 'epic_cinematic' / 'calm ambient' (first known token)."""
        m = (mood or "").strip().lower()
        if m in MOOD_ALIASES:
            return MOOD_ALIASES[m]
        for tok in re.split(r"[_\s/+-]+", m):
            if tok in MOOD_ALIASES:
                return MOOD_ALIASES[tok]
        return [m] if m else ["dramatic"]

    def _select(self, catalog: list[dict], mood: str, bpm_target: int) -> dict:
        moods = self._mood_group(mood)
        candidates = [t for t in catalog if t.get("mood") in moods]
        if not candidates:
            logger.warning(
                "music_curator: mood %r did not match any catalog track — "
                "falling back to full catalog by BPM", mood)
            candidates = catalog
        # Closest BPM within the mood group.
        return min(candidates, key=lambda t: abs(t.get("bpm", 100) - bpm_target))

    def _process(self, src: Path, dst: Path, target_len: float) -> None:
        """Loop/trim to target length with seamless crossfade, normalise gently."""
        from pydub import AudioSegment

        seg = AudioSegment.from_file(src)
        need_ms = int(target_len * 1000) + 500
        if len(seg) < need_ms:
            loops = (need_ms // max(1, len(seg))) + 1
            out = seg
            for _ in range(loops):
                out = out.append(seg, crossfade=min(3000, len(seg) // 2))
            seg = out
        seg = seg[:need_ms]
        if math.isfinite(seg.dBFS):  # dBFS is -inf for a fully-silent segment
            seg = seg.apply_gain(_TARGET_DBFS - seg.dBFS)
        seg = seg.fade_in(1500).fade_out(2500)
        # Normalise rate/channels so the downstream mux mixes at a single rate
        # (avoids implicit mid-graph resampling artifacts).
        seg = seg.set_frame_rate(44100).set_channels(2)
        seg.export(dst, format="mp3", bitrate="192k")

    def _beats(self, processed: Path, bpm: int, target_len: float) -> dict:
        try:
            import librosa  # type: ignore

            y, sr = librosa.load(str(processed), sr=22050, mono=True, duration=target_len)
            tempo, frames = librosa.beat.beat_track(y=y, sr=sr)
            times = librosa.frames_to_time(frames, sr=sr).tolist()
            return {"bpm": float(tempo), "beats": [round(t, 3) for t in times], "source": "librosa"}
        except Exception:
            interval = 60.0 / max(1, bpm)
            n = int(target_len / interval)
            return {"bpm": bpm, "beats": [round(i * interval, 3) for i in range(n)], "source": "estimated"}


# --- standalone test ---
if __name__ == "__main__":
    async def _demo():
        ctx = {
            "editing_plan": {"music": {"mood": "dramatic", "bpm_target": 95}},
            "narration": {"total_duration": 30.0},
        }
        agent = MusicCuratorAgent(job_id=0, context=ctx, emit=False)
        r = await agent.execute()
        print("track:", r["track"]["file"], "| processed:", r["processed_path"])
        print("beats source:", r["beat_timestamps"]["source"], "| count:", len(r["beat_timestamps"]["beats"]))

    asyncio.run(_demo())
