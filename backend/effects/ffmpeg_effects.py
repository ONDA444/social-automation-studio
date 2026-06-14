"""
FFmpeg effect library — pure filter-string builders, no side effects.

Color grades, camera moves (zoompan/pan), atmosphere (vignette/grain), audio
loudness, and transition descriptors. The VideoEditor composes these into its
filtergraph; each can also be tested in isolation:

    python -m backend.effects.ffmpeg_effects --test warm_golden input.mp4
    python -m backend.effects.ffmpeg_effects --list
"""
from __future__ import annotations

import subprocess
import sys

# ---------------------------------------------------------------- color grades
# Each value is an ffmpeg video-filter fragment applied to the stream.
COLOR_GRADES: dict[str, str] = {
    "natural": "eq=contrast=1.02:saturation=1.02",
    "warm_golden": "colorbalance=rs=0.06:gs=0.02:bs=-0.08:rm=0.05:bm=-0.06,eq=gamma=1.02:saturation=1.08",
    "cold_blue": "colorbalance=rs=-0.06:bs=0.10:rm=-0.04:bm=0.08,eq=saturation=1.05",
    "vintage": "curves=preset=vintage,eq=saturation=0.9",
    "high_contrast": "eq=contrast=1.30:saturation=1.10:brightness=-0.02",
    "noir": "hue=s=0,eq=contrast=1.25:brightness=0.02",
    "vibrant_pop": "eq=saturation=1.45:contrast=1.12:brightness=0.01",
    "muted_tones": "eq=saturation=0.72:contrast=0.98",
    "cyberpunk": "colorbalance=rs=0.10:bs=0.12:gm=-0.05:bm=0.10,eq=saturation=1.25:contrast=1.15",
}

# ---------------------------------------------------------------- atmosphere
ATMOSPHERE: dict[str, str] = {
    "vignette": "vignette=PI/4",
    "vignette_heavy": "vignette=PI/3.5",
    "subtle_grain": "noise=alls=4:allf=t",
    "film_grain": "noise=alls=9:allf=t",
    "lens_flare_subtle": "eq=brightness=0.02",  # cheap stand-in
    "high_contrast": "eq=contrast=1.18",
}

# ---------------------------------------------------------------- transitions
# Names map to ffmpeg xfade transitions; non-xfade ones are handled as concat.
XFADE_MAP: dict[str, str] = {
    "crossfade": "fade",
    "dissolve": "dissolve",
    "zoom_blur": "fadegrays",
    "slide_left": "slideleft",
    "whip_pan": "slideright",
    "radial_blur": "radial",
    "glitch": "pixelize",
    "fade": "fade",
}
HARD_TRANSITIONS = {"hard_cut", "flash_cut"}


def color_grade(name: str) -> str:
    return COLOR_GRADES.get(name, COLOR_GRADES["natural"])


def atmosphere(name: str) -> str | None:
    return ATMOSPHERE.get(name)


def camera_filter(name: str, duration: float, fps: int, w: int, h: int) -> str:
    """
    Build a zoompan/scale filter for a still image clip of `duration` seconds.
    Pre-scales to 2x to keep the zoom smooth (zoompan jitters on small inputs).
    """
    frames = max(1, int(round(duration * fps)))
    big_w, big_h = w * 2, h * 2
    pre = f"scale={big_w}:{big_h}:force_original_aspect_ratio=increase,crop={big_w}:{big_h}"
    zexpr_in = "min(zoom+0.0012,1.35)"
    zexpr_out = "if(eq(on,1),1.35,max(zoom-0.0012,1.0))"
    base = f"zoompan=d={frames}:s={w}x{h}:fps={fps}"
    if name == "ken_burns_zoom_in":
        zp = f"{base}:z='{zexpr_in}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
    elif name == "ken_burns_zoom_out":
        zp = f"{base}:z='{zexpr_out}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
    elif name == "pan_left":
        zp = f"{base}:z=1.2:x='max(0,iw-iw/zoom-(on/{frames})*(iw-iw/zoom))':y='ih/2-(ih/zoom/2)'"
    elif name == "pan_right":
        zp = f"{base}:z=1.2:x='(on/{frames})*(iw-iw/zoom)':y='ih/2-(ih/zoom/2)'"
    else:  # static
        zp = f"{base}:z=1.0:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
    return f"{pre},{zp},setsar=1"


def xfade_name(transition: str) -> str | None:
    """Return the ffmpeg xfade name, or None if this should be a hard cut."""
    if transition in HARD_TRANSITIONS:
        return None
    return XFADE_MAP.get(transition, "fade")


def loudnorm(platform: str = "youtube") -> str:
    """Target integrated loudness per platform."""
    target = "-14" if platform == "youtube" else "-9"  # tiktok/ig hotter
    return f"loudnorm=I={target}:TP=-1.5:LRA=11"


def build_scene_filter(
    color: str,
    camera: str,
    duration: float,
    fps: int,
    w: int,
    h: int,
    extra_atmosphere: list[str] | None = None,
) -> str:
    """Full per-scene video filter: camera -> color grade -> atmosphere -> pixfmt."""
    parts = [camera_filter(camera, duration, fps, w, h), color_grade(color)]
    for eff in extra_atmosphere or []:
        frag = atmosphere(eff)
        if frag:
            parts.append(frag)
    parts.append("format=yuv420p")
    return ",".join(parts)


def list_effects() -> dict:
    return {
        "color_grades": list(COLOR_GRADES),
        "atmosphere": list(ATMOSPHERE),
        "transitions": list(XFADE_MAP) + list(HARD_TRANSITIONS),
        "camera": ["ken_burns_zoom_in", "ken_burns_zoom_out", "pan_left", "pan_right", "static"],
    }


# ---- CLI: test a single effect on an input ----
def _test_effect(effect: str, input_path: str) -> int:
    out = f"effect_test_{effect}.mp4"
    if effect in COLOR_GRADES:
        vf = color_grade(effect)
    elif effect in ATMOSPHERE:
        vf = atmosphere(effect)
    else:
        print(f"Unknown effect '{effect}'. Use --list to see options.")
        return 2
    cmd = ["ffmpeg", "-y", "-i", input_path, "-vf", vf, "-t", "5", "-c:v", "libx264", out]
    print("Running:", " ".join(cmd))
    rc = subprocess.run(cmd).returncode
    print(("[OK] wrote " + out) if rc == 0 else "[FAIL] ffmpeg error")
    return rc


if __name__ == "__main__":
    args = sys.argv[1:]
    if "--list" in args:
        import json

        print(json.dumps(list_effects(), indent=2))
    elif "--test" in args:
        i = args.index("--test")
        effect = args[i + 1]
        input_path = args[i + 2] if len(args) > i + 2 else None
        if not input_path:
            print("Usage: --test <effect> <input.mp4|input.jpg>")
            sys.exit(2)
        sys.exit(_test_effect(effect, input_path))
    else:
        print("Usage: --list | --test <effect> <input>")
