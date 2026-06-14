"""
Generate original, royalty-free (CC0) ambient music beds with FFmpeg and write
music_catalog.json.

These are layered-sine pads — not chart-toppers, but genuinely original and
license-free, so the pipeline mixes real music out of the box. Replace any file
with a curated CC0 track (pixabay.com/music, mixkit.co, freemusicarchive.org)
and update the catalog entry; the schema is identical.

Run:  python assets/music/generate_music.py
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent
DUR = 40  # seconds per track (loopable by the curator)

# (file, mood, chord freqs, tremolo hz, bpm-ish, energy, extra_filter, best_for)
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


def build_track(cfg) -> dict:
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
    cmd = ["ffmpeg", "-y", *inputs, "-filter_complex", chain,
           "-map", "[a]", "-ar", "44100", "-ac", "2", "-b:a", "192k", str(out)]
    subprocess.run(cmd, capture_output=True, check=True)
    return {
        "file": fname,
        "mood": mood,
        "bpm": bpm,
        "energy": energy,
        "duration": DUR,
        "best_for": best_for,
        "license": "CC0 (synthesized original)",
    }


def main() -> None:
    catalog = []
    for cfg in TRACKS:
        try:
            entry = build_track(cfg)
            catalog.append(entry)
            print(f"[OK] {entry['file']} ({entry['mood']}, {entry['bpm']}bpm)")
        except subprocess.CalledProcessError as e:
            print(f"[FAIL] {cfg[0]}: {e.stderr.decode(errors='replace')[-200:] if e.stderr else e}")
    (HERE / "music_catalog.json").write_text(
        json.dumps(catalog, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(f"\nWrote music_catalog.json with {len(catalog)} tracks.")


if __name__ == "__main__":
    main()
