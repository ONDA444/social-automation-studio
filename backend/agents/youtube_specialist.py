"""YouTube Specialist — o "cara dos 10 anos de YouTube" dentro do pipeline.

Posição: roda UMA vez depois do packaging (roteiro + gancho + títulos + conceito
de thumb já existem). Ele audita o pacote com régua de quem vive de CTR e
retenção, e quando reprova aplica UM passe cirúrgico de correção via LLM
(gancho, título e conceito de thumb). Limitado a 1 revisão por vídeo para não
estourar cota de LLM.

Regras de segurança (nunca travar o pipeline):
- Sem LLM (offline/quota) → aprova em silêncio, como os growth agents fazem.
- Veredito malformado → aprova (nota 70).
- A correção nunca apaga cenas — só reescreve narração do gancho, título e
  conceito de thumb; o resto do roteiro passa intacto.
"""
from __future__ import annotations

import logging

from backend import llm
from backend.agents.base_agent import BaseAgent

logger = logging.getLogger("studio.youtube_specialist")

# Nota de corte: abaixo disso o pacote volta para 1 passe de correção.
APPROVE_SCORE = 70
MAX_REVISIONS = 1

_SYSTEM = """Você é o ESPECIALISTA DE YOUTUBE — 10 anos publicando, com dezenas de
canais dark e autorais monetizados. Você já viu milhares de curvas de retenção
e sabe exatamente onde o espectador abandona. Sua régua é fria e numérica.
Você avalia roteiro, gancho, títulos e conceito de thumbnail contra estas regras:

GANCHO (primeiros 3s falados):
- Proibido começar com saudação ("fala galera"), pedido de inscrição ou logo.
- Precisa: promessa concreta do payoff + motivo para ficar (gap de curiosidade,
  número, detalhe inquietante). Verbo no presente, frases curtas.
- Cada cena deve terminar puxando a próxima (mini-loop); nada de "e é isso".

RETENÇÃO:
- Re-gancho a cada 25-60s; payoff real antes dos 80% do vídeo (nunca só no fim).
- CTA de inscrição DEPOIS de entregar valor (meio/fim), nunca nos primeiros 10s.

TÍTULO (alto CTR, honesto):
- ≤60 caracteres, UMA fórmula por título (gap, número+risco, contrarian,
  detalhe, pergunta aberta, negação, ranking). Sem CAPS integral, ≤1 emoji.
- A promessa PRECISA ser verdadeira contra os fatos (sem clickbait falso).

THUMBNAIL (conceito, antes do render):
- ≤3 elementos, texto de overlay ≤4 palavras, legível em tela pequena.
- Complementa o título (não repete). Contraste forte, emoção/ação quando couber.

Responda SEMPRE em JSON válido, exatamente neste schema:
{"score": <0-100>, "verdict": "approve"|"revise", "notes": ["..."],
 "hook_narration": "<gancho reescrito, ou>",
 "title": "<melhor título, ou>",
 "title_options": ["...", "...", "..."],
 "thumb_concept": "<conceito corrigido, ou>",
 "thumb_text": "<texto overlay ≤4 palavras, ou>"}
Preencha os campos de correção SOMENTE se verdict=revise; se approve, deixe "".
Seja exigente como quem aposta o próprio CPM: nota <70 = revise."""


def _hook_text(script: dict) -> str:
    scenes = script.get("scenes") or []
    if scenes:
        return (scenes[0].get("narration") or "")[:400]
    return ""


def _brief(script: dict, packaging: dict, facts: str, language: str) -> str:
    titles = script.get("title_options") or [script.get("title", "")]
    scenes = script.get("scenes") or []
    tail = " | ".join((s.get("narration") or "")[:120] for s in scenes[1:4])
    return f"""Vídeo: "{script.get('title')}" ({script.get('content_type')}, idioma {language}).
GANCHO FALADO (cena 1): "{_hook_text(script)}"
MEIO (amostra): "{tail}"
TÍTULOS: {titles}
CONCEITO DE THUMB: {packaging.get('thumb_concept') or packaging.get('thumbnail_concept') or '—'}
TEXTO DA THUMB: {packaging.get('thumb_text') or '—'}
FATOS REAIS (não contradiga): {facts[:500]}"""


class YouTubeSpecialistAgent(BaseAgent):
    name = "youtube_specialist"

    async def run(self, script: dict | None = None, **_) -> dict:
        script = script or self.ctx_get("script") or {}
        packaging = self.ctx_get("packaging") or {}
        facts = (self.ctx_get("research") or {}).get("facts", "")
        language = (script.get("language")
                    or self.ctx_get("language") or "pt-BR")
        self.emit("progress", "Auditoria do especialista (roteiro + gancho + thumb)", progress=37)

        review: dict = {"verdict": "approved", "score": APPROVE_SCORE,
                        "notes": [], "revised": False}
        try:
            verdict = await llm.complete_json(
                _brief(script, packaging, facts, language)
                + "\n\nAvalie e responda no schema pedido.",
                system=_SYSTEM, max_tokens=1200,
            )
        except llm.LLMUnavailable:
            self._store(script, packaging, review)
            return review

        try:
            score = int(verdict.get("score", APPROVE_SCORE))
        except (TypeError, ValueError):
            score = APPROVE_SCORE
        notes = [str(n) for n in (verdict.get("notes") or [])][:6]
        review = {"verdict": verdict.get("verdict", "approved"),
                  "score": max(0, min(100, score)), "notes": notes,
                  "revised": False}

        if review["verdict"] == "revise" and review["score"] < APPROVE_SCORE:
            # UM passe cirúrgico: reescreve gancho/título/thumb, preserva o resto.
            hook = (verdict.get("hook_narration") or "").strip()
            if hook and script.get("scenes"):
                script["scenes"][0]["narration"] = hook
            title = (verdict.get("title") or "").strip()
            if title:
                script["title"] = title[:100]
            options = [t for t in (verdict.get("title_options") or []) if t]
            if options:
                script["title_options"] = options[:3]
                script["title"] = script.get("title") or options[0]
            thumb = (verdict.get("thumb_concept") or "").strip()
            if thumb:
                packaging["thumb_concept"] = thumb
                packaging["thumbnail_concept"] = thumb
            thumb_text = (verdict.get("thumb_text") or "").strip()
            if thumb_text:
                packaging["thumb_text"] = thumb_text
            review["revised"] = True
            review["score"] = max(review["score"], APPROVE_SCORE)
            self.emit("progress",
                      f"Especialista revisou ({review['score']}): " + "; ".join(notes[:2]),
                      progress=38)
        else:
            review["verdict"] = "approved"
            self.emit("progress", f"Especialista aprovou ({review['score']})", progress=38)

        self._store(script, packaging, review)
        return {**review, "script": script, "packaging": packaging}

    def _store(self, script: dict, packaging: dict, review: dict) -> None:
        self.ctx_set("script", script)
        self.ctx_set("packaging", packaging)
        self.ctx_set("specialist_review", {k: v for k, v in review.items()
                                           if k in ("verdict", "score", "notes", "revised")})
