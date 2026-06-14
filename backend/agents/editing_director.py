"""
EditingDirectorAgent — an LLM "director" decides every edit parameter up front,
producing an EditingPlan the VideoEditor executes. Falls back to a per-content-type
heuristic plan when no LLM is available.
"""
from __future__ import annotations

import asyncio

from backend.agents.base_agent import BaseAgent
from backend import llm

# Valid vocabularies the VideoEditor knows how to execute.
COLOR_GRADES = ["warm_golden", "cold_blue", "vintage", "high_contrast", "noir",
                "vibrant_pop", "muted_tones", "cyberpunk", "natural"]
TRANSITIONS = ["crossfade", "flash_cut", "zoom_blur", "slide_left", "whip_pan",
               "glitch", "dissolve", "hard_cut", "radial_blur"]
CAMERA = ["ken_burns_zoom_in", "ken_burns_zoom_out", "pan_left", "pan_right", "static"]

HEURISTICS = {
    "film_recap_ai_images": {
        "color_grade": "warm_golden",
        "transitions": {"default": "crossfade", "on_highlight": "zoom_blur"},
        "camera_effects": {"default": "ken_burns_zoom_in", "alt": "ken_burns_zoom_out"},
        "avg_clip_duration": 4.5,
        "beat_sync": True,
        "music": {"mood": "dramatic", "bpm_target": 95, "volume_db": -20, "swell_at_highlights": True},
        "text_style": "bold_impact",
        "intro_style": "dramatic_reveal",
        "outro_style": "subscribe_animation",
        "special_effects": ["vignette", "subtle_grain"],
    },
    "sports_highlights": {
        "color_grade": "vibrant_pop",
        "transitions": {"default": "flash_cut", "on_highlight": "whip_pan"},
        "camera_effects": {"default": "static", "alt": "ken_burns_zoom_in"},
        "avg_clip_duration": 2.5,
        "beat_sync": True,
        "music": {"mood": "energetic", "bpm_target": 128, "volume_db": -18, "swell_at_highlights": True},
        "text_style": "bold_impact",
        "intro_style": "fast_punch",
        "outro_style": "subscribe_animation",
        "special_effects": ["high_contrast"],
    },
    "quote_viral": {
        "color_grade": "noir",
        "transitions": {"default": "crossfade", "on_highlight": "crossfade"},
        "camera_effects": {"default": "ken_burns_zoom_in", "alt": "static"},
        "avg_clip_duration": 5.0,
        "beat_sync": False,
        "music": {"mood": "ambient", "bpm_target": 80, "volume_db": -16, "swell_at_highlights": False},
        "text_style": "bold_impact",
        "intro_style": "fade",
        "outro_style": "fade",
        "special_effects": ["vignette_heavy"],
    },
}

SYSTEM = "Você é um diretor de edição de vídeo profissional. Responda APENAS com JSON válido."


class EditingDirectorAgent(BaseAgent):
    name = "editing_director"

    async def run(
        self,
        script: dict | None = None,
        style_dna: dict | None = None,
        content_type: str = "film_recap_ai_images",
        **_,
    ) -> dict:
        script = script or self.ctx_get("script") or {}
        content_type = script.get("content_type", content_type)
        self.emit("progress", "Definindo EditingPlan", progress=68)

        try:
            plan = await self._via_llm(script, style_dna, content_type)
            plan = self._sanitize(plan, content_type)
            self.emit("progress", "EditingPlan via LLM", progress=70)
        except llm.LLMUnavailable:
            plan = dict(HEURISTICS.get(content_type, HEURISTICS["film_recap_ai_images"]))
            self.emit("progress", "EditingPlan heurístico (sem LLM)", progress=70)

        self.ctx_set("editing_plan", plan)
        return plan

    async def _via_llm(self, script, style_dna, content_type) -> dict:
        prompt = f"""Crie um EditingPlan em JSON para um vídeo do tipo {content_type},
tom "{script.get('tone')}", título "{script.get('title')}", {len(script.get('scenes', []))} cenas.
Estilo de referência (StyleDNA): {style_dna or 'nenhum'}.

Use SOMENTE estes valores permitidos:
color_grade: {COLOR_GRADES}
transitions: {TRANSITIONS}
camera_effects: {CAMERA}

Formato EXATO:
{{
  "color_grade": "...",
  "transitions": {{"default": "...", "on_highlight": "..."}},
  "camera_effects": {{"default": "...", "alt": "..."}},
  "avg_clip_duration": 4.5,
  "beat_sync": true,
  "music": {{"mood": "dramatic", "bpm_target": 95, "volume_db": -20, "swell_at_highlights": true}},
  "text_style": "bold_impact",
  "intro_style": "dramatic_reveal",
  "outro_style": "subscribe_animation",
  "special_effects": ["vignette", "subtle_grain"]
}}"""
        return await llm.complete_json(prompt, system=SYSTEM, max_tokens=800)

    @staticmethod
    def _sanitize(plan: dict, content_type: str) -> dict:
        """Clamp LLM output to known-executable values, filling gaps from heuristics."""
        base = dict(HEURISTICS.get(content_type, HEURISTICS["film_recap_ai_images"]))
        if plan.get("color_grade") in COLOR_GRADES:
            base["color_grade"] = plan["color_grade"]
        tr = plan.get("transitions", {})
        if tr.get("default") in TRANSITIONS:
            base["transitions"]["default"] = tr["default"]
        if tr.get("on_highlight") in TRANSITIONS:
            base["transitions"]["on_highlight"] = tr["on_highlight"]
        cam = plan.get("camera_effects", {})
        if cam.get("default") in CAMERA:
            base["camera_effects"]["default"] = cam["default"]
        if cam.get("alt") in CAMERA:
            base["camera_effects"]["alt"] = cam["alt"]
        try:
            base["avg_clip_duration"] = float(plan.get("avg_clip_duration", base["avg_clip_duration"]))
        except (TypeError, ValueError):
            pass
        if isinstance(plan.get("music"), dict):
            base["music"].update({k: v for k, v in plan["music"].items()})
        for key in ("beat_sync", "text_style", "intro_style", "outro_style", "special_effects"):
            if key in plan:
                base[key] = plan[key]
        return base


# --- standalone test ---
if __name__ == "__main__":
    import json

    async def _demo():
        script = {"title": "O farol", "tone": "dramatic",
                  "content_type": "film_recap_ai_images", "scenes": [{}, {}, {}]}
        agent = EditingDirectorAgent(job_id=0, emit=False)
        print(json.dumps(await agent.execute(script=script), ensure_ascii=False, indent=2))

    asyncio.run(_demo())
