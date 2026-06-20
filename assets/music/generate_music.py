"""
Generate original, royalty-free (CC0) music beds with FFmpeg and write
music_catalog.json.

Two flavours, all synthesized in-repo (no copyright, no external API):
  • Calm/ambient/dramatic moods  -> layered-sine PADS (build_pad).
  • Energetic/action/upbeat moods -> beat-driven HIGHLIGHT tracks (build_beat):
    a real kick on every beat, hats on the off-beat, a snare on the backbeat and
    pulsing chord stabs synced to the BPM — the "batida forte" highlight feel.

These are still synthetic, not chart-toppers. To use REAL tracks, drop a CC0 mp3
(pixabay.com/music, mixkit.co, freemusicarchive.org) into this folder and add a
matching entry to music_catalog.json — the schema is identical and the pipeline
picks it up automatically.

Run:  python assets/music/generate_music.py
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent
DUR = 40  # seconds per track (loopable by the curator)

# Moods that get a real beat instead of a static pad.
BEAT_MOODS = {"upbeat", "energetic", "action"}

# (file, mood, chord freqs, tremolo hz, bpm, energy, extra_filter, best_for)
TRACKS = [
    ("dramatic_1.mp3", "dramatic", [146.83, 174.61, 220.00], 1.2, 95, 0.6, "", ["film_recap_ai_images"]),
    ("dramatic_2.mp3", "dramatic", [130.81, 155.56, 196.00], 1.0, 90, 0.6, "", ["film_recap_ai_images"]),
    ("epic_1.mp3", "epic", [130.81, 164.81, 196.00], 1.5, 100, 0.75, "", ["film_recap_ai_images"]),
    ("epic_2.mp3", "epic", [146.83, 185.00, 220.00], 1.5, 110, 0.8, "", ["film_recap_ai_images"]),
    ("suspense_1.mp3", "suspense", [130.81, 185.00, 246.94], 0.8, 80, 0.5, "", ["film_recap_ai_images"]),
    ("suspense_2.mp3", "suspense", [123.47, 174.61, 233.08], 0.7, 75, 0.45, "", ["film_recap_ai_images"]),
    ("melancholic_1.mp3", "melancholic", [110.00, 130.81, 164.81], 0.6, 70, 0.35, "", ["quote_viral"]),
    ("melancholic_2.mp3", "melancholic", [98.00, 116.54, 146.83], 0.6, 65, 0.35, "", ["quote_viral"]),
    ("ambient_1.mp3", "ambient", [261.63, 329.63, 392.00], 0.5, 70, 0.3, "lowpass=f=2500", ["quote_viral"]),
    ("ambient_2.mp3", "ambient", [233.08, 293.66, 349.23], 0.5, 68, 0.3, "lowpass=f=2500", ["quote_viral"]),
    ("lofi_1.mp3", "lofi", [174.61, 220.00, 261.63], 2.0, 85, 0.4, "lowpass=f=1800", ["quote_viral"]),
    ("lofi_2.mp3", "lofi", [155.56, 196.00, 233.08], 2.0, 82, 0.4, "lowpass=f=1800", ["quote_viral"]),
    ("upbeat_1.mp3", "upbeat", [196.00, 246.94, 293.66], 4.0, 120, 0.7, "", ["sports_highlights"]),
    ("upbeat_2.mp3", "upbeat", [220.00, 277.18, 329.63], 4.0, 124, 0.7, "", ["sports_highlights"]),
    ("energetic_1.mp3", "energetic", [164.81, 207.65, 246.94], 6.0, 128, 0.85, "", ["sports_highlights"]),
    ("energetic_2.mp3", "energetic", [185.00, 233.08, 277.18], 6.0, 132, 0.85, "", ["sports_highlights"]),
    ("action_1.mp3", "action", [110.00, 164.81, 220.00], 7.0, 140, 0.9, "", ["sports_highlights"]),
    ("action_2.mp3", "action", [123.47, 185.00, 246.94], 7.0, 145, 0.95, "", ["sports_highlights"]),
    ("uplifting_1.mp3", "upbeat", [261.63, 329.63, 392.00], 3.0, 115, 0.65, "", ["sports_highlights", "quote_viral"]),
    ("calm_1.mp3", "ambient", [196.00, 246.94, 293.66], 0.4, 60, 0.25, "lowpass=f=2200", ["quote_viral"]),
]


def _entry(cfg) -> dict:
    fname, mood, _freqs, _trem, bpm, energy, _extra, best_for = cfg
    return {"file": fname, "mood": mood, "bpm": bpm, "energy": energy,
            "duration": DUR, "best_for": best_for, "license": "CC0 (synthesized original)"}


def build_pad(cfg) -> dict:
    """Layered-sine ambient bed (calm moods)."""
    fname, mood, freqs, trem, bpm, energy, extra, best_for = cfg
    out = HERE / fname
    inputs = []
    for f in freqs:
        inputs += ["-f", "lavfi", "-i", f"sine=frequency={f}:duration={DUR}"]
    mix = f"[0][1][2]amix=inputs={len(freqs)}:normalize=0"
    chain = f"{mix},tremolo=f={trem}:d=0.6,aecho=0.8:0.7:55:0.35"
    if extra:
        chain += f",{extra}"
    chain += f",afade=t=in:d=2,afade=t=out:st={DUR - 3}:d=3,volume=0.9,alimiter=limit=0.95[a]"
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", *inputs,
           "-filter_complex", chain, "-map", "[a]", "-ar", "44100", "-ac", "2", "-b:a", "192k", str(out)]
    subprocess.run(cmd, capture_output=True, check=True)
    return _entry(cfg)


def build_beat(cfg) -> dict:
    """Beat-driven highlight track: kick + off-beat hats + backbeat snare + pulsing
    chord stabs locked to the BPM. Envelopes use floor()-based phase (no commas) so
    the lavfi expressions parse cleanly as inputs."""
    fname, mood, freqs, _trem, bpm, energy, _extra, best_for = cfg
    out = HERE / fname
    p = 60.0 / bpm                       # one beat, seconds
    p2 = p * 2                           # two beats (backbeat cycle)
    beat_hz = bpm / 60.0

    inputs = []
    for f in freqs:                      # chord notes -> indices 0..n-1
        inputs += ["-f", "lavfi", "-i", f"sine=frequency={f}:duration={DUR}"]
    n = len(freqs)
    # phase(period) in [0,1): t/period - floor(t/period)
    kick = f"sin(2*PI*52*t)*exp(-23*{p}*(t/{p}-floor(t/{p})))"
    hat = f"(random(0)*2-1)*exp(-85*{p}*(t/{p}+0.5-floor(t/{p}+0.5)))"
    snare = f"(random(0)*2-1)*exp(-42*{p2}*(t/{p2}+0.5-floor(t/{p2}+0.5)))"
    inputs += ["-f", "lavfi", "-i", f"aevalsrc=exprs={kick}:d={DUR}:s=44100"]    # idx n
    inputs += ["-f", "lavfi", "-i", f"aevalsrc=exprs={hat}:d={DUR}:s=44100"]     # idx n+1
    inputs += ["-f", "lavfi", "-i", f"aevalsrc=exprs={snare}:d={DUR}:s=44100"]   # idx n+2

    chord_refs = "".join(f"[{i}]" for i in range(n))
    chain = (
        f"{chord_refs}amix=inputs={n}:normalize=0,tremolo=f={beat_hz:.3f}:d=0.45,volume=0.9[ch];"
        f"[{n}]volume=1.7[kick];"
        f"[{n + 1}]highpass=f=6500,volume=0.35[hat];"
        f"[{n + 2}]bandpass=f=1900:width_type=h:w=1400,volume=0.6[snare];"
        f"[ch][kick][hat][snare]amix=inputs=4:weights=1 1.7 0.35 0.65:normalize=0,"
        f"aecho=0.7:0.6:35:0.2,"
        f"afade=t=in:d=1.2,afade=t=out:st={DUR - 3}:d=3,alimiter=limit=0.96[a]"
    )
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", *inputs,
           "-filter_complex", chain, "-map", "[a]", "-ar", "44100", "-ac", "2", "-b:a", "192k", str(out)]
    subprocess.run(cmd, capture_output=True, check=True)
    return _entry(cfg)


def main() -> None:
    catalog = []
    for cfg in TRACKS:
        mood = cfg[1]
        builder = build_beat if mood in BEAT_MOODS else build_pad
        try:
            entry = builder(cfg)
            catalog.append(entry)
            kind = "beat" if mood in BEAT_MOODS else "pad"
            print(f"[OK] {entry['file']} ({entry['mood']}, {entry['bpm']}bpm, {kind})")
        except subprocess.CalledProcessError as e:
            print(f"[FAIL] {cfg[0]}: {e.stderr.decode(errors='replace')[-300:] if e.stderr else e}")
    (HERE / "music_catalog.json").write_text(
        json.dumps(catalog, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(f"\nWrote music_catalog.json with {len(catalog)} tracks.")


if __name__ == "__main__":
    main()
