"""
Fetch a curated CC0 (public-domain) real-music library from FreePD and rebuild
music_catalog.json.

Replaces the old SYNTHESIZED beds (generate_music.py — the "POC POC" placeholder
beats made with ffmpeg aevalsrc) with actual instrumental tracks. FreePD content
is CC0 1.0: free for commercial/monetized use on YouTube/TikTok, no attribution
required. Source: https://github.com/0lhi/FreePD (mirror of freepd.com, curated
by Kevin MacLeod et al.).

Run once to populate assets/music/ with real tracks:
    python assets/music/fetch_real_music.py

Idempotent: skips files already downloaded. Old synth .mp3s are moved to
assets/music/_synth_backup/ and the previous catalog is backed up alongside.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import urllib.parse
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
RAW = "https://raw.githubusercontent.com/0lhi/FreePD/stream/"

# (freepd_path, mood, bpm_estimate, energy, dest_name)
# bpm is an estimate only — the curator picks the closest-BPM track within a mood
# group, so relative ordering is what matters. Moods cover every value the
# EditingDirector / MOOD_ALIASES can request (dramatic, epic, suspense, tense,
# melancholic, ambient, calm, lofi, upbeat, uplifting, energetic, action).
TRACKS = [
    ("Epic/The Ice Giants.mp3",                 "dramatic",   90, 0.6, "freepd_dramatic_1.mp3"),
    ("Scoring/Patron Saint of Heists.mp3",      "dramatic",  100, 0.6, "freepd_dramatic_2.mp3"),
    ("Epic/Overture.mp3",                       "epic",      105, 0.7, "freepd_epic_1.mp3"),
    ("Epic/Fanfare X.mp3",                      "epic",      110, 0.7, "freepd_epic_2.mp3"),
    ("Epic/Battle Ready.mp3",                   "action",    140, 0.9, "freepd_action_1.mp3"),
    ("Electronic/Chronos.mp3",                  "action",    145, 0.9, "freepd_action_2.mp3"),
    ("Horror/Nightmare.mp3",                    "suspense",   80, 0.4, "freepd_suspense_1.mp3"),
    ("Horror/Footsteps in the Attic.mp3",       "suspense",   75, 0.4, "freepd_suspense_2.mp3"),
    ("Horror/Alien Spaceship Atmosphere.mp3",   "tense",      70, 0.4, "freepd_tense_1.mp3"),
    ("Romance/Landra's Dream.mp3",              "melancholic",65, 0.3, "freepd_melancholic_1.mp3"),
    ("Romance/Amazing Grace.mp3",               "melancholic",60, 0.3, "freepd_melancholic_2.mp3"),
    ("Scoring/Slice of Life.mp3",               "ambient",    70, 0.3, "freepd_ambient_1.mp3"),
    ("Scoring/City Run.mp3",                    "ambient",    90, 0.4, "freepd_ambient_2.mp3"),
    ("Romance/Horizon Flare.mp3",               "calm",       68, 0.2, "freepd_calm_1.mp3"),
    ("World/Coy Koi.mp3",                       "lofi",       82, 0.4, "freepd_lofi_1.mp3"),
    ("World/Forest Frolic Loop.mp3",            "lofi",       85, 0.4, "freepd_lofi_2.mp3"),
    ("Upbeat/And Just Like That.mp3",           "upbeat",    120, 0.7, "freepd_upbeat_1.mp3"),
    ("Upbeat/From Page to Practice.mp3",        "upbeat",    124, 0.7, "freepd_upbeat_2.mp3"),
    ("Upbeat/Stereotype News.mp3",              "uplifting", 115, 0.7, "freepd_uplifting_1.mp3"),
    ("Electronic/Hippety Hop.mp3",              "energetic", 128, 0.8, "freepd_energetic_1.mp3"),
    ("Electronic/Bit Bit Loop.mp3",             "energetic", 130, 0.8, "freepd_energetic_2.mp3"),
    ("Electronic/Backbeat.mp3",                 "energetic", 124, 0.8, "freepd_energetic_3.mp3"),
]


def _probe_duration(path: Path) -> float:
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
            capture_output=True, text=True, timeout=30,
        )
        return round(float(out.stdout.strip()), 1)
    except Exception:
        return 0.0


def main() -> None:
    # Back up old synthesized tracks + catalog so we can revert.
    backup = HERE / "_synth_backup"
    backup.mkdir(exist_ok=True)
    catalog_path = HERE / "music_catalog.json"
    if catalog_path.exists():
        shutil.copy2(catalog_path, backup / "music_catalog.json.bak")
        try:
            old = json.loads(catalog_path.read_text(encoding="utf-8"))
            for t in old:
                f = HERE / t.get("file", "")
                if f.exists() and not f.name.startswith("freepd_"):
                    shutil.move(str(f), str(backup / f.name))
        except Exception as exc:
            print("  (could not move old synth files:", exc, ")")

    catalog = []
    for src_path, mood, bpm, energy, dest_name in TRACKS:
        dest = HERE / dest_name
        if not dest.exists():
            url = RAW + urllib.parse.quote(src_path)
            print(f"↓ {dest_name:28s} <- {src_path}")
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "curl/8"})
                with urllib.request.urlopen(req, timeout=120) as r, open(dest, "wb") as fh:
                    shutil.copyfileobj(r, fh)
            except Exception as exc:
                print(f"  ! failed: {exc}")
                continue
        else:
            print(f"= {dest_name:28s} (already present)")
        dur = _probe_duration(dest)
        catalog.append({
            "file": dest_name,
            "mood": mood,
            "bpm": bpm,
            "energy": energy,
            "duration": dur,
            "source": f"FreePD: {src_path}",
            "license": "CC0 1.0 (public domain — free for commercial/monetized use, no attribution)",
        })

    catalog_path.write_text(json.dumps(catalog, ensure_ascii=False, indent=2), encoding="utf-8")
    size_mb = sum((HERE / t["file"]).stat().st_size for t in catalog) / 1e6
    print(f"\n✓ {len(catalog)} real CC0 tracks ({size_mb:.1f} MB) — catalog written to {catalog_path.name}")


if __name__ == "__main__":
    main()
