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

# rate per template family (fallback for unlisted types is settings.tts_rate)
RATE_BY_CONTENT = {
    "sports_highlights": "+15%",
    "film_recap_ai_images": "+8%",
    "quote_viral": "-10%",
}

VOICES = {
    "male": "pt-BR-AntonioNeural",
    "female": "pt-BR-FranciscaNeural",
}

# Same-gender, same-language edge-tts alternates. Microsoft's throttling of a Railway
# datacenter IP is often per-request/transient, so when the configured voice fails we
# try a SIBLING voice of the SAME gender before surrendering to gTTS — which has NO
# gender control and turns a male channel generic/female. Order = preference.
_EDGE_ALTS = {
    "pt-BR-AntonioNeural": ["pt-BR-FabioNeural", "pt-BR-DonatoNeural", "pt-BR-JulioNeural", "pt-BR-HumbertoNeural"],
    "pt-BR-FranciscaNeural": ["pt-BR-BrendaNeural", "pt-BR-GiovannaNeural", "pt-BR-LeticiaNeural", "pt-BR-YaraNeural"],
}


class NarratorAgent(BaseAgent):
    name = "narrator"

    async def run(
        self,
        script: dict | None = None,
        voice: str | None = None,
        content_type: str = "film_recap_ai_images",
        language: str | None = None,
        **_,
    ) -> dict:
        script = script or self.ctx_get("script") or {}
        content_type = script.get("content_type", content_type)
        language = language or self.ctx_get("language") or settings.default_language
        # Honour the channel's configured voice: explicit param > ctx > language default.
        voice = voice or self.ctx_get("voice") or self._default_voice_for(language)
        rate = RATE_BY_CONTENT.get(content_type, settings.tts_rate or "+8%")
        self._language = language
        # Voice-integrity tracking — which provider actually spoke, and whether we had to
        # fall back to a path that DROPS the requested voice/gender (gTTS / clone-failed).
        # The orchestrator reads these to avoid auto-publishing a wrong-voice video.
        self._tts_provider = "edge-tts"
        self._voice_fallback_used = False
        self._voice_requested = voice

        out_dir = self.job_dir(self.job_id, settings.abs_path(settings.output_dir))
        audio_path = out_dir / "narration.mp3"
        ts_path = out_dir / "narration_timestamps.json"

        # Prefer tts_text (already stripped of direction markers) over narration_text.
        # If tts_text is absent or still contains brackets, fall back to narration_text.
        tts_candidate = (script.get("tts_text") or "").strip()
        if tts_candidate and "[" not in tts_candidate:
            narration_text = tts_candidate
        else:
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
            "voice_requested": voice,
            "tts_provider": self._tts_provider,
            "voice_fallback_used": self._voice_fallback_used,
            "rate": rate,
        }
        ts_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        self.ctx_set("narration", payload)
        self.emit("progress", f"Narração pronta: {total:.1f}s, {len(words)} palavras", progress=55)
        return payload

    @staticmethod
    def _default_voice_for(language: str) -> str:
        """edge-tts voice matching the channel language (used when LMNT doesn't apply,
        e.g. English/foreign channels). The account's preferred_voice overrides this."""
        lang = (language or "pt-BR").lower()
        root = lang.split("-")[0]
        # For the default (pt) language, honour the configured DEFAULT_TTS_VOICE so the
        # operator can set e.g. a female default without touching code. Other languages
        # use a sensible matched voice (the configured default may be pt-only).
        if root == "pt":
            return settings.default_tts_voice or "pt-BR-AntonioNeural"
        table = {
            "en": "en-US-GuyNeural", "es": "es-ES-AlvaroNeural",
            "fr": "fr-FR-HenriNeural", "de": "de-DE-ConradNeural", "it": "it-IT-DiegoNeural",
        }
        return table.get(root, settings.default_tts_voice)

    async def _synthesize(self, text: str, voice: str, rate: str, audio_path: Path):
        """Provider chain: LMNT cloned voice -> edge-tts fallback.

        The LMNT clone (MATEUS) is a PORTUGUESE voice, so it's only used for pt
        channels. English/other-language channels go straight to a language-matched
        edge-tts voice — otherwise the pt clone would speak English with a pt accent.
        """
        lang = (getattr(self, "_language", None) or settings.default_language or "pt-BR").lower()
        explicit_clone = bool(voice) and voice.startswith("v_")

        # FREE per-channel preset voice (an edge-tts voice NAME, e.g. pt-BR-FabioNeural)
        # is authoritative — synthesize it DIRECTLY. No LMNT round-trip (LMNT is paid /
        # often out of credits), and each channel can sound distinct for $0.
        if voice and not explicit_clone:
            return await self._synthesize_edge(text, voice, rate, audio_path)

        # Cloned "v_..." voice -> LMNT (paid). If LMNT is unavailable / out of credits,
        # fall back to a language-matched FREE edge voice so a video still gets a voice
        # (degrade gracefully instead of failing the job).
        if (explicit_clone and settings.lmnt_api_key
                and (settings.tts_provider or "auto").lower() in ("auto", "lmnt")):
            try:
                self.emit("progress", f"Sintetizando voz clonada LMNT ({voice})", progress=45)
                result = await self._synthesize_lmnt(text, rate, audio_path, lang, voice)
                self._tts_provider = "lmnt"
                return result
            except Exception as exc:  # noqa: BLE001
                # The cloned voice could NOT be produced; the language-default edge voice
                # below is a different person (possibly a different gender). Flag it so the
                # job goes to Approval instead of auto-publishing the wrong voice.
                self._voice_fallback_used = True
                self.emit("progress", f"LMNT indisponível ({exc}); voz padrão grátis (voz clonada NÃO usada)", progress=45)

        # No voice set / clone unavailable -> language-matched free edge voice.
        return await self._synthesize_edge(text, self._default_voice_for(lang), rate, audio_path)

    async def _synthesize_lmnt(self, text: str, rate: str, audio_path: Path,
                               language: str = "pt-BR", voice_id: str | None = None):
        """LMNT official API. Word timings are distributed proportionally over the
        real audio length (good enough for caption sync; no source voice cloned)."""
        speed = self._rate_to_speed(rate)
        payload = {
            "voice": voice_id or settings.lmnt_voice,
            "text": text,
            "format": "mp3",
            "sample_rate": 24000,
            "language": (language or "pt-BR").split("-")[0],
            "speed": speed,
        }
        # LMNT 5xx are frequently transient — retry a few times before letting the
        # caller fall back to edge-tts, so a single hiccup doesn't silently swap
        # the user's cloned voice for the default one.
        last_exc: Exception | None = None
        async with httpx.AsyncClient(timeout=180) as client:
            for attempt in range(4):
                try:
                    r = await client.post(LMNT_BYTES_URL, json=payload,
                                          headers={"X-API-Key": settings.lmnt_api_key})
                    if r.status_code >= 500:
                        raise RuntimeError(f"LMNT {r.status_code} (transitório)")
                    r.raise_for_status()
                    if not r.content or len(r.content) < 1000:
                        raise RuntimeError("áudio LMNT vazio")
                    audio_path.write_bytes(r.content)
                    last_exc = None
                    break
                except Exception as exc:  # noqa: BLE001
                    last_exc = exc
                    if attempt < 3:
                        await asyncio.sleep(1.5 * (attempt + 1))
        if last_exc is not None:
            raise last_exc
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
        """edge-tts with a gTTS safety net.

        Microsoft frequently blocks / throttles datacenter IPs (Railway) and the
        edge-tts client then raises "No audio was received" — which used to kill the
        whole video. gTTS (Google Translate TTS) is also free and keyless, served from
        a different provider, so when Microsoft refuses we still get a voice instead of
        failing the job. gTTS has no word boundaries, so its timings are distributed
        proportionally (same approach as the LMNT path).

        Microsoft's throttling of datacenter IPs is usually TRANSIENT and often
        per-voice/per-request, so we try the requested voice AND same-gender sibling
        voices (preserving the channel's gender/identity) with backoff BEFORE giving up.
        Only when EVERY edge voice fails do we fall back to gTTS — which has no gender
        control, so a male channel would come out generic/female; that path is FLAGGED
        (voice_fallback_used) so the orchestrator won't auto-publish it unattended."""
        candidates = [voice] + [v for v in _EDGE_ALTS.get(voice, []) if v and v != voice]
        last_exc: Exception | None = None
        for cand in candidates:
            for attempt in range(2):
                try:
                    result = await self._edge_stream(text, cand, rate, audio_path)
                    self._tts_provider = "edge-tts"
                    if cand != voice:
                        self.emit("progress",
                                  f"Voz preferida indisponível; usando voz do MESMO gênero ({cand})", progress=45)
                    return result
                except Exception as exc:  # noqa: BLE001
                    last_exc = exc
                    if attempt < 1:
                        await asyncio.sleep(1.2 * (attempt + 1))
        # Every edge voice failed → gTTS (genderless). Flag the loss of voice identity.
        self.emit("progress",
                  f"edge-tts indisponível ({last_exc}); reserva gTTS — voz GENÉRICA pt-BR, "
                  "gênero/voz configurada NÃO preservada", progress=45)
        self._tts_provider = "gtts"
        self._voice_fallback_used = True
        return await self._synthesize_gtts(text, voice, audio_path, rate)

    async def _edge_stream(self, text: str, voice: str, rate: str, audio_path: Path):
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
        # edge-tts can silently yield no audio bytes (network blip / throttling).
        # An empty MP3 would pass as "success" and produce a silent video — raise so
        # the gTTS fallback (or BaseAgent.execute retry) kicks in.
        if not audio_path.exists() or audio_path.stat().st_size < 1024:
            raise RuntimeError("edge-tts retornou áudio vazio")
        total = words[-1]["end"] if words else self._probe_duration(audio_path)
        if total <= 0:
            raise RuntimeError("edge-tts: duração inválida (áudio sem conteúdo)")
        return words, round(total, 3)

    async def _synthesize_gtts(self, text: str, voice: str, audio_path: Path, rate: str = "+0%"):
        """Free, keyless fallback (Google Translate TTS). Runs in a worker thread —
        gTTS is synchronous — and estimates word timings proportionally.

        gTTS has no percentage-based rate control (unlike edge-tts/LMNT), only a
        binary slow=True/False. RATE_BY_CONTENT's pacing intent would otherwise be
        silently dropped whenever the pipeline degrades to this last-resort fallback.
        We can't reproduce +15%/+8% (gTTS has no "faster" mode), but a strongly
        negative rate (e.g. quote_viral's -10%) is mapped to slow=True so the
        channel doesn't lose its "slow/deliberate" pacing entirely.
        """
        from gtts import gTTS

        lang = self._gtts_lang(voice)
        tld = "com.br" if lang == "pt" else "com"
        slow = self._rate_to_speed(rate) <= 0.9

        def _write() -> None:
            gTTS(text=text, lang=lang, tld=tld, slow=slow).save(str(audio_path))

        await asyncio.to_thread(_write)
        if not audio_path.exists() or audio_path.stat().st_size < 1024:
            raise RuntimeError("gTTS retornou áudio vazio")
        total = self._probe_duration(audio_path)
        if total <= 0:
            total = max(1.0, len(text.split()) / 2.5)
        return self._proportional_words(text, total), round(total, 3)

    @staticmethod
    def _gtts_lang(voice: str) -> str:
        """Derive a gTTS language code from the edge voice name (pt-BR-Antonio -> pt)."""
        v = (voice or "pt-BR").lower()
        for code in ("pt", "en", "es", "fr", "de", "it"):
            if v.startswith(code):
                return code
        return "pt"

    @staticmethod
    def _derive_markers(script: dict, words: list[dict]) -> list[dict]:
        """Tag the approximate start time of each highlight scene."""
        from backend.agents.scriptwriter import clean_markers

        markers: list[dict] = []
        scenes = script.get("scenes", [])
        # Build a flat word list with scene boundaries. `words` is timestamped from
        # the SPOKEN audio, which is synthesized from the marker-free text (tts_text) —
        # so word counts here must be taken from the cleaned narration too, or the
        # cursor drifts past every [RE-HOOK]/[PAUSA]/[ENFASE]/[LOOP-*] token.
        cursor = 0
        for sc in scenes:
            n = len(clean_markers(sc.get("narration") or "").split())
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
