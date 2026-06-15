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
import shutil
import subprocess
from pathlib import Path

from backend.agents.base_agent import BaseAgent
from backend.config import settings
from backend.effects import ffmpeg_effects as fx

W, H, FPS = 1920, 1080, 30
TRANSITION_DUR = 0.4  # seconds of xfade overlap

# Capped-bitrate H.264 profile (~8-12 Mbps target; keeps grain from exploding size).
VENC = ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "veryfast",
        "-crf", "21", "-maxrate", "12M", "-bufsize", "24M"]


class FFmpegError(RuntimeError):
    pass


def _run(cmd: list[str], cwd: str | None = None) -> None:
    proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace")
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
        fmt = (self.ctx_get("format") or script.get("format") or "long")
        self.W, self.H = (1080, 1920) if fmt == "short" else (W, H)

        out_dir = self.job_dir(self.job_id, settings.abs_path(settings.output_dir))
        work = self.job_dir(self.job_id, settings.abs_path(settings.temp_dir)) / "edit"
        if work.exists():
            shutil.rmtree(work)
        work.mkdir(parents=True, exist_ok=True)

        durations = self._scene_durations(scene_assets, narration, script, editing_plan)
        self.emit("progress", f"Renderizando {len(scene_assets)} clipes", progress=72)

        jid = "adhoc" if self.job_id is None else self.job_id
        final_path = out_dir / f"video_{jid}_main.mp4"
        await asyncio.to_thread(
            self._render, scene_assets, durations, editing_plan, narration, music, captions, work, final_path
        )

        result = {"main_video_path": str(final_path), "duration": round(sum(durations), 2),
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

        # 1) Render each scene clip (video only).
        clip_files: list[str] = []
        for i, (asset, dur) in enumerate(zip(scene_assets, durations)):
            clip = work / f"clip_{i:03d}.mp4"
            cam = cam_default if i % 2 == 0 else cam_alt
            if asset.get("type") == "video":
                self._render_video_scene(asset["path"], clip, dur, color, atmos)
            else:
                self._render_image_scene(asset["path"], clip, dur, cam, color, atmos)
            clip_files.append(clip.name)
            self.emit("progress", f"Clipe {i + 1}/{len(scene_assets)}", progress=72)

        # 2) Stitch (xfade chain or concat) + optional caption burn -> silent video.
        default_tr = plan.get("transitions", {}).get("default", "crossfade")
        ass_name = self._stage_captions(captions, work)
        silent = work / "stitched.mp4"
        self._stitch(clip_files, durations, default_tr, ass_name, work, silent)

        # 3) Build audio bed.
        video_len = self._probe_duration(silent)
        audio_final = work / "audio_final.m4a"
        has_audio = self._build_audio(narration, music, plan, video_len, work, audio_final)

        # 4) Mux.
        cmd = ["ffmpeg", "-y", "-i", silent.name]
        if has_audio:
            cmd += ["-i", audio_final.name, "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
                    "-map", "0:v:0", "-map", "1:a:0", "-shortest"]
        else:
            cmd += ["-c:v", "copy"]
        cmd.append(str(final_path))
        _run(cmd, cwd=str(work))

    def _render_image_scene(self, src: str, dst: Path, dur: float, cam: str, color: str, atmos):
        vf = fx.build_scene_filter(color, cam, dur, FPS, self.W, self.H, atmos)
        cmd = ["ffmpeg", "-y", "-loop", "1", "-i", src, "-t", f"{dur:.3f}",
               "-vf", vf, "-r", str(FPS), *VENC, str(dst)]
        _run(cmd)

    def _render_video_scene(self, src: str, dst: Path, dur: float, color: str, atmos):
        grade = fx.color_grade(color)
        atmos_frags = ",".join(filter(None, (fx.atmosphere(a) for a in atmos)))
        chain = f"scale={self.W}:{self.H}:force_original_aspect_ratio=increase,crop={self.W}:{self.H},{grade}"
        if atmos_frags:
            chain += f",{atmos_frags}"
        chain += ",format=yuv420p,setsar=1"
        cmd = ["ffmpeg", "-y", "-stream_loop", "-1", "-i", src, "-t", f"{dur:.3f}",
               "-vf", chain, "-an", "-r", str(FPS), *VENC, str(dst)]
        _run(cmd)

    def _stitch(self, clip_files, durations, transition, ass_name, work: Path, dst: Path):
        n = len(clip_files)
        soft = fx.xfade_name(transition)

        if n == 1:
            vf = f"ass={ass_name}" if ass_name else None
            cmd = ["ffmpeg", "-y", "-i", clip_files[0]]
            if vf:
                cmd += ["-vf", vf]
            cmd += [*VENC, dst.name]
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
            # narration + ducked music
            cmd = ["ffmpeg", "-y", "-i", narr_path, "-stream_loop", "-1", "-i", music_path,
                   "-filter_complex",
                   f"[1:a]volume={vol_db}dB[bg];"
                   f"[bg][0:a]sidechaincompress=threshold=0.03:ratio=8:attack=20:release=300[duck];"
                   f"[0:a][duck]amix=inputs=2:duration=first:dropout_transition=2,{loud}[a]",
                   "-map", "[a]", "-t", f"{video_len:.3f}", "-c:a", "aac", "-b:a", "192k", dst.name]
            _run(cmd, cwd=str(work))
            return True

        if music_path:
            cmd = ["ffmpeg", "-y", "-stream_loop", "-1", "-i", music_path,
                   "-filter_complex", f"[0:a]volume={vol_db}dB,{loud}[a]",
                   "-map", "[a]", "-t", f"{video_len:.3f}", "-c:a", "aac", "-b:a", "192k", dst.name]
            _run(cmd, cwd=str(work))
            return True

        # narration only
        cmd = ["ffmpeg", "-y", "-i", narr_path, "-af", loud,
               "-t", f"{video_len:.3f}", "-c:a", "aac", "-b:a", "192k", dst.name]
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
    def _probe_duration(path: Path) -> float:
        try:
            out = subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries", "format=duration",
                 "-of", "default=nw=1:nk=1", str(path)],
                capture_output=True, text=True,
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
