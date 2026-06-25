"""
CaptionAgent — styled captions as an ASS file (burned in by the VideoEditor).

Uses the word-level timestamps from NarratorAgent to build 3-5 word blocks.
For quote_viral (no narration) it renders the chosen on-screen text centered.

Styles: minimal | bold_impact | highlight_words | karaoke | tiktok_box | none
Modes:  burn_in (default, via ass=) | srt | ass
"""
from __future__ import annotations

import asyncio
from pathlib import Path

from backend.agents.base_agent import BaseAgent
from backend.config import settings

# ASS colors are &HAABBGGRR (alpha, blue, green, red).
STYLE_PRESETS = {
    "minimal":        {"font": "Arial", "size": 54, "primary": "&H00FFFFFF", "outline": 2, "bold": 0, "border": 1, "back": "&H64000000"},
    "bold_impact":    {"font": "Arial", "size": 64, "primary": "&H00FFFFFF", "outline": 4, "bold": 1, "border": 1, "back": "&H00000000"},
    "highlight_words":{"font": "Arial", "size": 60, "primary": "&H00FFFFFF", "outline": 3, "bold": 1, "border": 1, "back": "&H00000000"},
    "tiktok_box":     {"font": "Arial", "size": 58, "primary": "&H00FFFFFF", "outline": 0, "bold": 1, "border": 3, "back": "&HB4000000"},
    "karaoke":        {"font": "Arial", "size": 60, "primary": "&H00FFFFFF", "outline": 3, "bold": 1, "border": 1, "back": "&H00000000"},
}
HIGHLIGHT_COLOR = "&H0000F0FF"  # yellow (BGR)


def sanitize_ass_text(s: str) -> str:
    """Escape user/LLM text so it can't break the ASS Dialogue Text field.

    In ASS, '{...}' delimits an override block — raw braces in the content
    make ffmpeg drop or corrupt the line. Escape braces and turn real line
    breaks into the ASS hard-break token. Apply ONLY to content text, never
    to the style override tags the code injects itself.
    """
    return (
        str(s)
        .replace("{", "\\{")
        .replace("}", "\\}")
        .replace("\r\n", "\\N")
        .replace("\n", "\\N")
        .replace("\r", "\\N")
    )


class CaptionAgent(BaseAgent):
    name = "caption_agent"

    async def run(
        self,
        narration: dict | None = None,
        script: dict | None = None,
        style: str = "bold_impact",
        mode: str = "burn_in",
        **_,
    ) -> dict:
        narration = narration or self.ctx_get("narration") or {}
        script = script or self.ctx_get("script") or {}
        if style == "none":
            self.ctx_set("captions", {"ass_path": None, "style": "none"})
            return {"ass_path": None, "style": "none"}

        out_dir = self.job_dir(self.job_id, settings.abs_path(settings.output_dir))
        ass_path = out_dir / "captions.ass"

        words = narration.get("words") or []
        if words:
            blocks = self._group_words(words)
            body = self._dialogue_lines(blocks, style)
        else:
            # quote_viral: show on-screen text for the whole clip.
            texts = script.get("on_screen_text") or [script.get("title", "")]
            dur = float(narration.get("total_duration") or script.get("estimated_duration") or 10)
            body = self._quote_lines(texts[0], dur, style)

        ass = self._header(style) + body
        ass_path.write_text(ass, encoding="utf-8")

        result = {"ass_path": str(ass_path), "style": style, "mode": mode}
        # Always emit an SRT alongside the burned-in ASS whenever we have word timings:
        # the ASS is for the render, the SRT is uploaded to YouTube as a REAL caption
        # track (search-indexable transcript + CC + free auto-translation = the biggest
        # free international-reach lever). Cheap text file; harmless when unused.
        if words:
            srt_path = out_dir / "captions.srt"
            srt_path.write_text(self._srt(self._group_words(words)), encoding="utf-8")
            result["srt_path"] = str(srt_path)

        self.ctx_set("captions", result)
        self.emit("progress", f"Legendas geradas ({style})", progress=71)
        return result

    # ---- grouping ----
    @staticmethod
    def _group_words(words: list[dict], per_block: int = 4) -> list[dict]:
        blocks = []
        for i in range(0, len(words), per_block):
            chunk = words[i : i + per_block]
            blocks.append({
                "start": chunk[0]["start"],
                "end": chunk[-1]["end"],
                "words": [w["word"] for w in chunk],
            })
        return blocks

    def _dialogue_lines(self, blocks: list[dict], style: str) -> str:
        lines = []
        for b in blocks:
            text = " ".join(sanitize_ass_text(w) for w in b["words"])
            if style == "highlight_words":
                text = self._highlight(b["words"])
            elif style == "karaoke":
                text = self._karaoke(b)
            lines.append(
                f"Dialogue: 0,{self._ts(b['start'])},{self._ts(b['end'])},Default,,0,0,0,,{text}"
            )
        return "\n".join(lines) + "\n"

    @staticmethod
    def _highlight(words: list[str]) -> str:
        out = []
        for w in words:
            sw = sanitize_ass_text(w)
            # Highlight longer (content) words.
            if len(w.strip(".,!?")) >= 6:
                out.append(f"{{\\c{HIGHLIGHT_COLOR}}}{sw}{{\\c&H00FFFFFF&}}")
            else:
                out.append(sw)
        return " ".join(out)

    @staticmethod
    def _karaoke(block: dict) -> str:
        # \k duration is in centiseconds; approximate even split.
        n = len(block["words"])
        total_cs = max(1, int((block["end"] - block["start"]) * 100))
        per = max(1, total_cs // n)
        return "".join(f"{{\\k{per}}}{sanitize_ass_text(w)} " for w in block["words"]).strip()

    def _quote_lines(self, text: str, duration: float, style: str) -> str:
        text = sanitize_ass_text(text)
        return f"Dialogue: 0,{self._ts(0)},{self._ts(duration)},Default,,0,0,0,,{text}\n"

    # ---- formats ----
    @staticmethod
    def _ts(seconds: float) -> str:
        h = int(seconds // 3600)
        m = int((seconds % 3600) // 60)
        s = int(seconds % 60)
        cs = int(round((seconds - int(seconds)) * 100))
        if cs == 100:
            cs = 99
        return f"{h}:{m:02d}:{s:02d}.{cs:02d}"

    def _header(self, style: str) -> str:
        p = STYLE_PRESETS.get(style, STYLE_PRESETS["bold_impact"])
        # Match the render frame so captions aren't stretched/mis-placed: vertical
        # 9:16 for Shorts, else 16:9. Vertical lifts captions higher (MarginV) so
        # they clear the bottom platform UI, and enlarges the font a touch.
        vertical = (self.ctx_get("format") or "long") == "short"
        rw, rh = (1080, 1920) if vertical else (1920, 1080)
        margin_v = 420 if vertical else 90
        size = int(p["size"] * (1.25 if vertical else 1.0))
        # Alignment 2 = bottom-center.
        return (
            "[Script Info]\n"
            "ScriptType: v4.00+\n"
            f"PlayResX: {rw}\nPlayResY: {rh}\nWrapStyle: 0\n\n"
            "[V4+ Styles]\n"
            "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, "
            "BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, "
            "BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
            f"Style: Default,{p['font']},{size},{p['primary']},&H000000FF,&H00000000,"
            f"{p['back']},{p['bold']},0,0,0,100,100,0,0,{p['border']},{p['outline']},1,2,80,80,{margin_v},1\n\n"
            "[Events]\n"
            "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
        )

    def _srt(self, blocks: list[dict]) -> str:
        out = []
        for i, b in enumerate(blocks, 1):
            out.append(f"{i}\n{self._srt_ts(b['start'])} --> {self._srt_ts(b['end'])}\n"
                       f"{' '.join(b['words'])}\n")
        return "\n".join(out)

    @staticmethod
    def _srt_ts(seconds: float) -> str:
        h = int(seconds // 3600)
        m = int((seconds % 3600) // 60)
        s = int(seconds % 60)
        ms = int(round((seconds - int(seconds)) * 1000))
        return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


# --- standalone test ---
if __name__ == "__main__":
    async def _demo():
        narration = {
            "total_duration": 6.0,
            "words": [
                {"word": "Esta", "start": 0.1, "end": 0.4}, {"word": "é", "start": 0.4, "end": 0.5},
                {"word": "uma", "start": 0.5, "end": 0.7}, {"word": "narração", "start": 0.7, "end": 1.2},
                {"word": "incrível", "start": 1.2, "end": 1.8}, {"word": "de", "start": 1.8, "end": 1.9},
                {"word": "teste", "start": 1.9, "end": 2.3},
            ],
        }
        for style in ["bold_impact", "highlight_words", "karaoke"]:
            agent = CaptionAgent(job_id=0, emit=False)
            r = await agent.execute(narration=narration, style=style)
            print(style, "->", r["ass_path"])
        print(Path(r["ass_path"]).read_text(encoding="utf-8")[:600])

    asyncio.run(_demo())
