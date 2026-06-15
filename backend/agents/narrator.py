"""
NarratorAgent — edge-tts narration (free, no API key) + word-level timestamps.

Produces, per job:
  output/job_<id>/narration.mp3
  output/job_<id>/narration_timestamps.json
      { total_duration, words:[{word,start,end}], markers:[{marker,timestamp}] }

We capture word boundaries from edge-tts's stream so CaptionAgent can sync
captions exactly. We deliberately do NOT strip internal silences (that would
desync the word timestamps); loudness is normalised later in the final mix.
"""
from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

import edge_tts
import httpx

from backend.agents.base_agent import BaseAgent
from backend.config import settings

LMNT_BYTES_URL = "https://api.lmnt.com/v1/ai/speech/bytes"

# rate per template family
RATE_BY_CONTENT = {
    "sports_highlights": "+15%",
    "film_recap_ai_images": "+0%",
    "quote_viral": "-10%",
}

VOICES = {
    "male": "pt-BR-AntonioNeural",
    "female": "pt-BR-FranciscaNeural",
}


class NarratorAgent(BaseAgent):
    name = "narrator"

    async def run(
        self,
        script: dict | None = None,
        voice: str | None = None,
        content_type: str = "film_recap_ai_images",
        **_,
    ) -> dict:
        script = script or self.ctx_get("script") or {}
        content_type = script.get("content_type", content_type)
        voice = voice or settings.default_tts_voice
        rate = RATE_BY_CONTENT.get(content_type, "+0%")

        out_dir = self.job_dir(self.job_id, settings.abs_path(settings.output_dir))
        audio_path = out_dir / "narration.mp3"
        ts_path = out_dir / "narration_timestamps.json"

        narration_text = (script.get("narration_text") or "").strip()

        # No voice-over when: quote_viral, empty text, OR a remix whose reference
        # had no narration (narrate=False) — the remix follows that music-driven
        # style. Emit a silent track sized to the planned video length.
        narrate = self.ctx_get("narrate")
        if narrate is False or content_type == "quote_viral" or not narration_text:
            if narrate is False:
                duration = self._silent_duration(script)
                msg = "Narração desligada — remix sem voz (segue o estilo da referência)"
            else:
                duration = float(script.get("estimated_duration", 10))
                msg = "Narração silenciosa (sem voz)"
            self._write_silence(audio_path, duration)
            payload = {"total_duration": duration, "words": [], "markers": [],
                       "audio_path": str(audio_path), "silent": True}
            ts_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            self.ctx_set("narration", payload)
            self.emit("progress", msg, progress=55)
            return payload

        self.emit("progress", f"Sintetizando voz ({voice}, rate={rate})", progress=45)
        words, total = await self._synthesize(narration_text, voice, rate, audio_path)

        # Map scene highlight markers to approximate timestamps (per-scene start).
        markers = self._derive_markers(script, words)

        payload = {
            "total_duration": total,
            "words": words,
            "markers": markers,
            "audio_path": str(audio_path),
            "silent": False,
            "voice": voice,
            "rate": rate,
        }
        ts_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        self.ctx_set("narration", payload)
        self.emit("progress", f"Narração pronta: {total:.1f}s, {len(words)} palavras", progress=55)
        return payload

    async def _synthesize(self, text: str, voice: str, rate: str, audio_path: Path):
        """Provider chain: LMNT (your cloned voice) -> edge-tts fallback."""
        use_lmnt = (settings.lmnt_api_key and settings.lmnt_voice
                    and (settings.tts_provider or "auto").lower() in ("auto", "lmnt"))
        if use_lmnt:
            try:
                self.emit("progress", f"Sintetizando voz LMNT ({settings.lmnt_voice})", progress=45)
                return await self._synthesize_lmnt(text, rate, audio_path)
            except Exception as exc:  # noqa: BLE001
                self.emit("progress", f"LMNT falhou ({exc}); usando edge-tts", progress=45)
        return await self._synthesize_edge(text, voice, rate, audio_path)

    async def _synthesize_lmnt(self, text: str, rate: str, audio_path: Path):
        """LMNT official API. Word timings are distributed proportionally over the
        real audio length (good enough for caption sync; no source voice cloned)."""
        speed = self._rate_to_speed(rate)
        payload = {
            "voice": settings.lmnt_voice,
            "text": text,
            "format": "mp3",
            "sample_rate": 24000,
            "language": (settings.default_language or "pt-BR").split("-")[0],
            "speed": speed,
        }
        async with httpx.AsyncClient(timeout=180) as client:
            r = await client.post(LMNT_BYTES_URL, json=payload,
                                  headers={"X-API-Key": settings.lmnt_api_key})
            r.raise_for_status()
            if not r.content or len(r.content) < 1000:
                raise RuntimeError("áudio LMNT vazio")
            audio_path.write_bytes(r.content)
        total = self._probe_duration(audio_path)
        if total <= 0:
            # Bytes were written OK but the probe failed (ffmpeg/pydub quirk). Estimate
            # from word count (~2.5 wps) instead of throwing away the premium render.
            total = max(1.0, len(text.split()) / 2.5)
            self.emit("progress", "LMNT: duração estimada (probe indisponível)", progress=45)
        return self._proportional_words(text, total), round(total, 3)

    @staticmethod
    def _rate_to_speed(rate: str) -> float:
        """'+15%' -> 1.15, '-10%' -> 0.9, '+0%' -> 1.0 (LMNT accepts 0.25–2.0)."""
        try:
            pct = int(rate.replace("%", "").replace("+", ""))
        except (ValueError, AttributeError):
            pct = 0
        return max(0.25, min(2.0, 1.0 + pct / 100.0))

    @staticmethod
    def _proportional_words(text: str, total: float) -> list[dict]:
        raw = text.split()
        if not raw:
            return []
        weights = [max(1, len(w)) for w in raw]
        tot = sum(weights)
        words, t = [], 0.0
        for w, wt in zip(raw, weights):
            d = total * wt / tot
            words.append({"word": w, "start": round(t, 3), "end": round(t + d, 3)})
            t += d
        return words

    async def _synthesize_edge(self, text: str, voice: str, rate: str, audio_path: Path):
        # edge-tts >=7 defaults to SentenceBoundary; we need word-level for captions.
        communicate = edge_tts.Communicate(text, voice, rate=rate, boundary="WordBoundary")
        words: list[dict] = []
        with open(audio_path, "wb") as f:
            async for chunk in communicate.stream():
                if chunk["type"] == "audio":
                    f.write(chunk["data"])
                elif chunk["type"] == "WordBoundary":
                    # offsets/durations are in 100-ns ticks.
                    start = chunk["offset"] / 1e7
                    dur = chunk["duration"] / 1e7
                    words.append({
                        "word": chunk["text"],
                        "start": round(start, 3),
                        "end": round(start + dur, 3),
                    })
        total = words[-1]["end"] if words else self._probe_duration(audio_path)
        return words, round(total, 3)

    @staticmethod
    def _derive_markers(script: dict, words: list[dict]) -> list[dict]:
        """Tag the approximate start time of each highlight scene."""
        markers: list[dict] = []
        scenes = script.get("scenes", [])
        # Build a flat word list with scene boundaries.
        cursor = 0
        for sc in scenes:
            n = len((sc.get("narration") or "").split())
            if n == 0:
                continue
            if cursor < len(words):
                ts = words[cursor]["start"]
                if sc.get("is_highlight"):
                    markers.append({"marker": "DESTAQUE", "timestamp": ts, "scene": sc.get("index")})
            cursor += n
        return markers

    def _silent_duration(self, script: dict) -> float:
        """Length for a no-voice remix: scene count x the reference's avg clip
        duration, so the music-driven video keeps the reference's pacing."""
        dna = self.ctx_get("style_dna") or {}
        avg = (dna.get("pacing") or {}).get("avg_clip_duration")
        n = len(script.get("scenes") or [])
        if avg and n:
            return round(max(8.0, min(90.0, n * float(avg))), 1)
        return float(script.get("estimated_duration", 30))

    @staticmethod
    def _write_silence(path: Path, seconds: float) -> None:
        from pydub import AudioSegment

        AudioSegment.silent(duration=int(seconds * 1000)).export(path, format="mp3")

    @staticmethod
    def _probe_duration(path: Path) -> float:
        try:
            from pydub import AudioSegment

            return len(AudioSegment.from_file(path)) / 1000.0
        except Exception as exc:  # noqa: BLE001
            logging.getLogger("studio.narrator").debug("probe_duration failed for %s: %s", path, exc)
            return 0.0


# --- standalone test: python -m backend.agents.narrator --test ---
if __name__ == "__main__":
    async def _demo():
        script = {
            "content_type": "film_recap_ai_images",
            "narration_text": "Esta é uma narração de teste. O farol abandonado guardava um segredo. "
                              "Ninguém sabia o que havia lá dentro.",
            "estimated_duration": 12,
            "scenes": [
                {"index": 0, "narration": "Esta é uma narração de teste.", "is_highlight": True},
                {"index": 1, "narration": "O farol abandonado guardava um segredo.", "is_highlight": False},
                {"index": 2, "narration": "Ninguém sabia o que havia lá dentro.", "is_highlight": True},
            ],
        }
        agent = NarratorAgent(job_id=0, emit=False)
        result = await agent.execute(script=script)
        print("duration:", result["total_duration"])
        print("words:", len(result["words"]), "| first 5:", result["words"][:5])
        print("markers:", result["markers"])
        print("audio:", result["audio_path"])

    asyncio.run(_demo())
