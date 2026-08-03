"""
VideoEditorAgent — executes the EditingPlan with FFmpeg.

Pipeline:
  1. Compute per-scene durations (synced to narration word-timestamps, else avg).
  2. Render each scene asset (image -> Ken Burns clip, or stock video -> trimmed)
     applying camera move + color grade + atmosphere per the EditingPlan.
  3. Stitch clips with xfade (soft transitions) or concat (hard cuts), optionally
     burning the caption ASS.
  4. Build the audio bed: narration + ducked background music, loudnorm to spec.
  5. Mux to output/job_<id>/video_<id>_main.mp4 (1920x1080 H.264 / AAC 192k / 30fps).

All effect work runs in a worker thread so it never blocks the event loop.
"""
from __future__ import annotations

import asyncio
import json
import re
import shutil
import subprocess
from pathlib import Path

from backend.agents.base_agent import BaseAgent
from backend.config import settings
from backend.effects import ffmpeg_effects as fx

# Landscape dimensions derived from the configured render height (default 720p).
# Lower height => far less memory in the multi-clip xfade (avoids container OOM).
FPS = 30
_LH = max(360, min(1080, settings.video_resolution))
H = _LH
W = (round(_LH * 16 / 9)) & ~1  # even width for yuv420p
# At Full HD a multi-clip xfade graph decodes every clip at once and OOM-kills a
# small container. Above this height we FORCE hard cuts regardless of the
# VIDEO_TRANSITIONS flag — concat streams one clip at a time, so 1080p stays
# memory-flat (each clip is still rendered in its own ffmpeg pass, one at a time).
_FORCE_HARD_CUT = _LH >= 1080
TRANSITION_DUR = 0.4  # seconds of xfade overlap
# Beyond this many clips, a single all-inputs xfade graph decodes too many videos
# at once and OOM-kills the container — fall back to (memory-light) hard cuts.
MAX_XFADE_CLIPS = 6

# Capped-bitrate H.264 profile (~8-12 Mbps target; keeps grain from exploding size).
# -threads caps x264's thread count: left to auto it spawns one per HOST core (60+
# on Railway) and the per-thread memory blows the container limit -> SIGKILL (rc=-9).
VENC = ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "veryfast",
        "-crf", "21", "-maxrate", "12M", "-bufsize", "24M",
        "-threads", str(max(1, settings.ffmpeg_threads))]


class FFmpegError(RuntimeError):
    pass


# ---- ASS timestamp helpers (H:MM:SS.cs, matches CaptionAgent's format) ----
_ASS_TS_RE = re.compile(r"(\d+):(\d{2}):(\d{2})\.(\d{2})")


def _parse_ass_ts(ts: str) -> float:
    m = _ASS_TS_RE.match(ts.strip())
    if not m:
        return 0.0
    h, mm, s, cs = (int(x) for x in m.groups())
    return h * 3600 + mm * 60 + s + cs / 100


def _format_ass_ts(seconds: float) -> str:
    seconds = max(0.0, seconds)
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    cs = int(round((seconds - int(seconds)) * 100))
    if cs == 100:
        cs = 99
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


# Hard ceiling per ffmpeg invocation. A healthy encode of one clip/segment is far
# under this; the point is that a HUNG ffmpeg (stalled filter, bad input) can never
# hold the single render slot forever — it's killed and surfaced as a retryable error.
_FFMPEG_TIMEOUT = 1200  # 20 min


def _run(cmd: list[str], cwd: str | None = None, timeout: int = _FFMPEG_TIMEOUT) -> None:
    try:
        proc = subprocess.run(
            cmd, cwd=cwd, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        # subprocess.run already killed the child; turn the stall into a retryable
        # FFmpegError so BaseAgent can retry/ERROR and the render semaphore is freed.
        raise FFmpegError(f"ffmpeg timed out after {timeout}s (killed): {' '.join(cmd[:3])}…")
    if proc.returncode != 0:
        tail = (proc.stderr or "")[-1500:]
        raise FFmpegError(f"ffmpeg failed (rc={proc.returncode}):\n{tail}")


class VideoEditorAgent(BaseAgent):
    name = "video_editor"

    async def run(
        self,
        visuals: dict | None = None,
        narration: dict | None = None,
        editing_plan: dict | None = None,
        music: dict | None = None,
        captions: dict | None = None,
        script: dict | None = None,
        **_,
    ) -> dict:
        visuals = visuals or self.ctx_get("visuals") or {}
        narration = narration or self.ctx_get("narration") or {}
        editing_plan = editing_plan or self.ctx_get("editing_plan") or {}
        music = music or self.ctx_get("music") or {}
        captions = captions or self.ctx_get("captions") or {}
        script = script or self.ctx_get("script") or {}

        scene_assets = visuals.get("scene_assets", [])
        if not scene_assets:
            raise FFmpegError("Nenhum asset de cena para editar.")

        # Target frame: native vertical (9:16) for Shorts, else landscape (16:9).
        # Portrait just swaps the configured dimensions (720p -> 720x1280).
        fmt = (self.ctx_get("format") or script.get("format") or "long")
        self.W, self.H = (H, W) if fmt == "short" else (W, H)

        out_dir = self.job_dir(self.job_id, settings.abs_path(settings.output_dir))
        work = self.job_dir(self.job_id, settings.abs_path(settings.temp_dir)) / "edit"
        if work.exists():
            shutil.rmtree(work)
        work.mkdir(parents=True, exist_ok=True)

        durations = self._scene_durations(scene_assets, narration, script, editing_plan)
        self.emit("progress", f"Renderizando {len(scene_assets)} clipes", progress=72)

        jid = "adhoc" if self.job_id is None else self.job_id
        final_path = out_dir / f"video_{jid}_main.mp4"
        video_len = await asyncio.to_thread(
            self._render, scene_assets, durations, editing_plan, narration, music, captions, work, final_path
        )

        result = {"main_video_path": str(final_path), "duration": round(video_len, 2),
                  "resolution": f"{self.W}x{self.H}", "fps": FPS, "format": fmt}
        self.ctx_set("main_video", result)
        self.emit("progress", f"Vídeo principal pronto ({result['duration']}s)", progress=82)
        return result

    # ---- duration planning ----
    def _scene_durations(self, scene_assets, narration, script, plan) -> list[float]:
        n = len(scene_assets)
        words = narration.get("words") or []
        total_audio = narration.get("total_duration") or 0
        scenes = script.get("scenes", [])
        avg = float(plan.get("avg_clip_duration", 4.5))

        if total_audio and words and scenes and len(scenes) == n:
            # Distribute total audio across scenes by their word counts.
            counts = [max(1, len((s.get("narration") or "").split())) for s in scenes]
            tot = sum(counts)
            durs = [max(1.5, total_audio * c / tot) for c in counts]
            # Ensure visual covers audio (pad last scene).
            drift = total_audio - sum(durs)
            if drift > 0:
                durs[-1] += drift
            return durs
        if total_audio:
            return [total_audio / n] * n
        return [avg] * n

    # ---- rendering ----
    def _render(self, scene_assets, durations, plan, narration, music, captions, work: Path, final_path: Path):
        color = plan.get("color_grade", "natural")
        cam_default = plan.get("camera_effects", {}).get("default", "ken_burns_zoom_in")
        cam_alt = plan.get("camera_effects", {}).get("alt", "ken_burns_zoom_out")
        atmos = plan.get("special_effects", [])

        # Resolve the stitch mode up front. A hard cut (the common path for
        # long-form content -- see MAX_XFADE_CLIPS) lets each scene burn its own
        # caption slice during the per-clip render pass below, so the final
        # concat can stream-copy instead of re-encoding the whole video a
        # second time just to draw captions on top.
        default_tr = plan.get("transitions", {}).get("default", "crossfade")
        ass_name = self._stage_captions(captions, work)
        hard_cut = self._resolve_soft_transition(default_tr, len(scene_assets)) is None
        clip_ass = self._slice_captions(ass_name, durations, work) if hard_cut else None

        # 1) Render each scene clip (video only).
        clip_files: list[str] = []
        for i, (asset, dur) in enumerate(zip(scene_assets, durations)):
            clip = work / f"clip_{i:03d}.mp4"
            cam = cam_default if i % 2 == 0 else cam_alt
            ass = clip_ass[i] if clip_ass else None
            if asset.get("type") == "video":
                self._render_video_scene(asset["path"], clip, dur, color, atmos, ass)
            else:
                self._render_image_scene(asset["path"], clip, dur, cam, color, atmos, ass)
            clip_files.append(clip.name)
            self.emit("progress", f"Clipe {i + 1}/{len(scene_assets)}", progress=72)

        # 2) Stitch (xfade chain or concat) -> silent video. Captions are
        # already burned in per-clip when clip_ass is set; otherwise burn
        # them here (soft xfade re-encodes the whole thing anyway).
        silent = work / "stitched.mp4"
        self._stitch(clip_files, durations, default_tr, None if clip_ass else ass_name, work, silent)

        # 3) Build audio bed.
        video_len = self._probe_duration(silent)
        audio_final = work / "audio_final.m4a"
        has_audio = self._build_audio(narration, music, plan, video_len, work, audio_final)

        # 4) Mux.
        # +faststart moves the moov atom to the front of the file — without it
        # ffmpeg defaults to writing it at the end, which some platforms'
        # ingest pipelines handle worse than others. Cheap, riskless change
        # applied to the exact file we upload (identified as a real risk
        # factor, though unconfirmed as THE cause, in the investigation into
        # videos stuck permanently "processing" on YouTube).
        cmd = ["ffmpeg", "-y", "-i", silent.name]
        if has_audio:
            cmd += ["-i", audio_final.name, "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
                    "-map", "0:v:0", "-map", "1:a:0", "-shortest", "-movflags", "+faststart"]
        else:
            cmd += ["-c:v", "copy", "-movflags", "+faststart"]
        cmd.append(str(final_path))
        _run(cmd, cwd=str(work))
        return video_len

    def _render_image_scene(self, src: str, dst: Path, dur: float, cam: str, color: str, atmos, ass: str | None = None):
        vf = fx.build_scene_filter(color, cam, dur, FPS, self.W, self.H, atmos)
        if ass:
            vf += f",ass={ass}"
        cmd = ["ffmpeg", "-y", "-loop", "1", "-i", src, "-t", f"{dur:.3f}",
               "-vf", vf, "-r", str(FPS), *VENC, str(dst)]
        # cwd=dst.parent so a per-clip `ass` filename (relative, staged in the
        # same work dir) resolves without needing to escape the absolute path
        # inside the filtergraph (breaks on Windows drive letters like "C:").
        _run(cmd, cwd=str(dst.parent))

    def _render_video_scene(self, src: str, dst: Path, dur: float, color: str, atmos, ass: str | None = None):
        grade = fx.color_grade(color)
        atmos_frags = ",".join(filter(None, (fx.atmosphere(a) for a in atmos)))
        chain = f"scale={self.W}:{self.H}:force_original_aspect_ratio=increase,crop={self.W}:{self.H},{grade}"
        if atmos_frags:
            chain += f",{atmos_frags}"
        chain += ",format=yuv420p,setsar=1"
        if ass:
            chain += f",ass={ass}"
        cmd = ["ffmpeg", "-y", "-stream_loop", "-1", "-i", src, "-t", f"{dur:.3f}",
               "-vf", chain, "-an", "-r", str(FPS), *VENC, str(dst)]
        _run(cmd, cwd=str(dst.parent))

    def _stitch(self, clip_files, durations, transition, ass_name, work: Path, dst: Path):
        n = len(clip_files)
        soft = self._resolve_soft_transition(transition, n)

        if n == 1:
            cmd = ["ffmpeg", "-y", "-i", clip_files[0]]
            if ass_name:
                cmd += ["-vf", f"ass={ass_name}", *VENC]
            else:
                cmd += ["-c", "copy"]
            cmd.append(dst.name)
            _run(cmd, cwd=str(work))
            return

        if soft is None:
            # Hard cut: concat demuxer.
            lst = work / "concat.txt"
            lst.write_text("".join(f"file '{c}'\n" for c in clip_files), encoding="utf-8")
            cmd = ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", "concat.txt"]
            if ass_name:
                cmd += ["-vf", f"ass={ass_name}", *VENC]
            else:
                cmd += ["-c", "copy"]
            cmd.append(dst.name)
            _run(cmd, cwd=str(work))
            return

        # Soft xfade chain.
        inputs: list[str] = []
        for c in clip_files:
            inputs += ["-i", c]
        t = TRANSITION_DUR
        filt = []
        prev = "0:v"
        offset = durations[0] - t
        for i in range(1, n):
            out = f"v{i}" if i < n - 1 or ass_name else ("vout" if not ass_name else f"v{i}")
            filt.append(f"[{prev}][{i}:v]xfade=transition={soft}:duration={t}:offset={max(0.1, offset):.3f}[{out}]")
            prev = out
            offset += durations[i] - t
        if ass_name:
            filt.append(f"[{prev}]ass={ass_name}[vout]")
            prev = "vout"
        cmd = ["ffmpeg", "-y", *inputs, "-filter_complex", ";".join(filt),
               "-map", f"[{prev}]", *VENC, dst.name]
        _run(cmd, cwd=str(work))

    def _build_audio(self, narration, music, plan, video_len, work: Path, dst: Path) -> bool:
        narr_path = narration.get("audio_path")
        music_path = (music or {}).get("processed_path") or (music or {}).get("path")
        is_silent = narration.get("silent", False)
        if not narr_path and not music_path:
            return False

        vol_db = (plan.get("music", {}) or {}).get("volume_db", -20)
        loud = fx.loudnorm("youtube")

        if narr_path and music_path and not is_silent:
            # narration + ducked music. Resample BOTH inputs to 44.1 kHz first:
            # TTS comes out at 16/22/24 kHz and the music at 44.1 kHz, and mixing
            # mismatched rates makes ffmpeg resample implicitly mid-graph — the
            # source of the grating/robotic artifact. [narr] is reused as the
            # sidechain trigger and as a mix input.
            cmd = ["ffmpeg", "-y", "-i", narr_path, "-stream_loop", "-1", "-i", music_path,
                   "-filter_complex",
                   f"[0:a]aresample=44100[narr];"
                   f"[1:a]aresample=44100,volume={vol_db}dB[bg];"
                   f"[bg][narr]sidechaincompress=threshold=0.03:ratio=8:attack=20:release=300[duck];"
                   f"[narr][duck]amix=inputs=2:duration=first:dropout_transition=2,{loud}[a]",
                   "-map", "[a]", "-t", f"{video_len:.3f}",
                   "-ar", "44100", "-ac", "2", "-c:a", "aac", "-b:a", "192k", dst.name]
            _run(cmd, cwd=str(work))
            return True

        if music_path:
            # No narration -> music is the LEAD. Don't apply the duck level; just
            # normalize so the track plays at full foreground loudness (the remix
            # follows a music-driven reference with no voice-over).
            cmd = ["ffmpeg", "-y", "-stream_loop", "-1", "-i", music_path,
                   "-filter_complex", f"[0:a]aresample=44100,{loud}[a]",
                   "-map", "[a]", "-t", f"{video_len:.3f}",
                   "-ar", "44100", "-ac", "2", "-c:a", "aac", "-b:a", "192k", dst.name]
            _run(cmd, cwd=str(work))
            return True

        # narration only
        cmd = ["ffmpeg", "-y", "-i", narr_path, "-af", f"aresample=44100,{loud}",
               "-t", f"{video_len:.3f}",
               "-ar", "44100", "-ac", "2", "-c:a", "aac", "-b:a", "192k", dst.name]
        _run(cmd, cwd=str(work))
        return True

    @staticmethod
    def _stage_captions(captions: dict, work: Path) -> str | None:
        ass_path = (captions or {}).get("ass_path")
        if not ass_path or not Path(ass_path).exists():
            return None
        local = work / "captions.ass"
        shutil.copyfile(ass_path, local)
        return local.name

    @staticmethod
    def _resolve_soft_transition(transition: str, n: int) -> str | None:
        """Return the xfade name the stitch will use, or None for a hard cut.

        Pure function of (transition, clip count) — mirrors the fallback rules
        in `_stitch` itself, so `_render` can decide ahead of the per-clip
        render pass whether captions should burn per-clip (hard cut) or on the
        final stitch (soft xfade, which re-encodes regardless).
        """
        if n <= 1:
            return None
        soft = fx.xfade_name(transition)
        # Soft xfade decodes every clip of the segment simultaneously (filter_complex
        # with all inputs) — the memory spike that OOM-kills a small container. Use it
        # only when explicitly enabled AND the clip count is small; otherwise hard cuts
        # (concat demuxer, one clip at a time) keep memory flat.
        if soft is not None and (_FORCE_HARD_CUT or not settings.video_transitions or n > MAX_XFADE_CLIPS):
            soft = None
        return soft

    @staticmethod
    def _slice_captions(ass_name: str | None, durations: list[float], work: Path) -> list[str | None] | None:
        """Split the full-video ASS into one file per scene clip.

        Each Dialogue event is clipped to its scene's [offset, offset+dur)
        window and rebased to clip-local time (0 == clip start). An event
        straddling a cut is duplicated, trimmed, into both neighboring clips —
        same on-screen result as burning the untouched ASS on the stitched
        video, just spread across the per-clip render passes instead of a
        second full-video encode.
        """
        if not ass_name:
            return None
        lines = (work / ass_name).read_text(encoding="utf-8").splitlines()
        split_at = next((i for i, l in enumerate(lines) if l.startswith("Dialogue:")), len(lines))
        header = "\n".join(lines[:split_at])
        events = []
        for line in lines[split_at:]:
            if not line.startswith("Dialogue:"):
                continue
            fields = line[len("Dialogue:"):].strip().split(",", 9)
            if len(fields) < 10:
                continue
            events.append((_parse_ass_ts(fields[1]), _parse_ass_ts(fields[2]), fields))

        clip_ass: list[str | None] = []
        offset = 0.0
        for i, dur in enumerate(durations):
            win_start, win_end = offset, offset + dur
            clip_lines = []
            for start, end, fields in events:
                if end <= win_start or start >= win_end:
                    continue
                local_start = max(0.0, start - win_start)
                local_end = min(dur, end - win_start)
                if local_end <= local_start:
                    continue
                f = list(fields)
                f[1] = _format_ass_ts(local_start)
                f[2] = _format_ass_ts(local_end)
                clip_lines.append("Dialogue:" + ",".join(f))
            if clip_lines:
                name = f"captions_{i:03d}.ass"
                (work / name).write_text(header + "\n" + "\n".join(clip_lines) + "\n", encoding="utf-8")
                clip_ass.append(name)
            else:
                clip_ass.append(None)
            offset += dur
        return clip_ass

    @staticmethod
    def _probe_duration(path: Path) -> float:
        try:
            out = subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries", "format=duration",
                 "-of", "default=nw=1:nk=1", str(path)],
                capture_output=True, text=True, timeout=30,
            )
            return float(out.stdout.strip())
        except Exception:
            return 0.0


# --- standalone test: python -m backend.agents.video_editor --test ---
if __name__ == "__main__":
    async def _demo():
        # Reuse assets from the visuals + narrator demos (run those first).
        out = settings.abs_path(settings.temp_dir) / "job_0" / "assets"
        narr = settings.abs_path(settings.output_dir) / "job_0" / "narration_timestamps.json"
        narration = json.loads(narr.read_text(encoding="utf-8")) if narr.exists() else {}
        scene_assets = [
            {"index": i, "path": str(p), "type": "image", "source": "pollinations"}
            for i, p in enumerate(sorted(out.glob("scene_*.jpg")))
        ]
        if not scene_assets:
            print("Run `python -m backend.agents.visuals --test` first to create assets.")
            return
        ctx = {
            "visuals": {"scene_assets": scene_assets},
            "narration": narration,
            "editing_plan": __import__("backend.agents.editing_director", fromlist=["HEURISTICS"]).HEURISTICS["film_recap_ai_images"],
        }
        agent = VideoEditorAgent(job_id=0, context=ctx, emit=False)
        result = await agent.execute()
        print("RESULT:", result)

    asyncio.run(_demo())
