"""YouTube Specialist — auditoria com régua de CTR/retenção + 1 passe de correção.

Garante: aprova direto quando a nota passa; aplica a correção cirúrgica quando
reprova (sem apagar cenas); nunca trava o pipeline (sem LLM ou veredito
estranho = aprovado); o hook/título/thumb corrigidos voltam no dict.
"""
from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from backend.agents import youtube_specialist as spec
from backend.agents.youtube_specialist import YouTubeSpecialistAgent


def _script() -> dict:
    return {
        "title": "Como aumentar o FPS",
        "title_options": ["Como aumentar o FPS", "FPS alto já", "Config secreta"],
        "content_type": "explainer_curiosity",
        "language": "pt-BR",
        "scenes": [
            {"narration": "Fala galera, se inscreve aí que hoje vou mostrar umas configs."},
            {"narration": "Primeiro abre o painel e desliga isso aqui."},
            {"narration": "No fim você vai ver a diferença no jogo."},
        ],
    }


def _agent():
    ctx = {"packaging": {"thumb_concept": "print de tela do painel",
                         "thumb_text": "CONFIG FPS 2026 ULTRA DETALHADA AGORA"},
           "research": {"facts": ""}}
    return YouTubeSpecialistAgent(job_id=None, context=ctx, emit=False)


class SpecialistTests(unittest.TestCase):
    def test_approve_keeps_everything(self) -> None:
        agent = _agent()
        verdict = {"score": 85, "verdict": "approve", "notes": ["ok"],
                   "hook_narration": "", "title": "", "title_options": [],
                   "thumb_concept": "", "thumb_text": ""}
        with patch.object(spec.llm, "complete_json", AsyncMock(return_value=verdict)):
            out = asyncio.run(agent.run(script=_script()))
        self.assertEqual(out["verdict"], "approved")
        self.assertFalse(out["revised"])
        self.assertEqual(len(out["script"]["scenes"]), 3)
        self.assertEqual(agent.context["specialist_review"]["score"], 85)

    def test_revise_applies_surgical_fix(self) -> None:
        agent = _agent()
        verdict = {"score": 55, "verdict": "revise",
                   "notes": ["gancho com saudação", "título genérico"],
                   "hook_narration": "Seu FPS dobra com 1 ajuste — te mostro em 20 segundos.",
                   "title": "1 ajuste dobra seu FPS",
                   "title_options": ["1 ajuste dobra seu FPS", "FPS dobrado já"],
                   "thumb_concept": "medidor de FPS gigante subindo",
                   "thumb_text": "FPS DOBRADO"}
        with patch.object(spec.llm, "complete_json", AsyncMock(return_value=verdict)):
            out = asyncio.run(agent.run(script=_script()))
        self.assertTrue(out["revised"])
        self.assertIn("dobra", out["script"]["scenes"][0]["narration"])
        self.assertEqual(out["script"]["title"], "1 ajuste dobra seu FPS")
        self.assertEqual(len(out["script"]["scenes"]), 3)  # nada apagado
        self.assertEqual(out["packaging"]["thumb_text"], "FPS DOBRADO")

    def test_no_llm_approves_silently(self) -> None:
        from backend import llm as _llm

        agent = _agent()
        with patch.object(spec.llm, "complete_json",
                          AsyncMock(side_effect=_llm.LLMUnavailable("sem cota"))):
            out = asyncio.run(agent.run(script=_script()))
        self.assertEqual(out["verdict"], "approved")
        self.assertFalse(out["revised"])

    def test_garbage_verdict_defaults_to_approve(self) -> None:
        agent = _agent()
        with patch.object(spec.llm, "complete_json", AsyncMock(return_value={"nada": 1})):
            out = asyncio.run(agent.run(script=_script()))
        self.assertEqual(out["verdict"], "approved")


if __name__ == "__main__":
    unittest.main()
