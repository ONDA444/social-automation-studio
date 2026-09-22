"""
ComplianceAgent — platform-policy pre-checks after QC, before human approval.

Outcome:
  approved  -> goes to the human approval queue (possibly with suggestions)
  blocked   -> returns to the user for manual fix (job stays out of the queue)

YouTube policy layer (deterministic, zero LLM cost):
- AI disclosure: nossos vídeos têm narração TTS e/ou imagens IA — o YouTube
  exige marcar "conteúdo alterado" no Studio (a API não marca sozinha), então
  todo vídeo sintético sai com a ação pendente em suggestions + flag
  `ai_disclosure_required` para a UI.
- Reused content (YPP): vídeo do Drive SEM a camada de curadoria (re-upload
  puro, sem comentário próprio) é o padrão que o YouTube desmonetiza como
  "conteúdo reutilizado" — isso BLOQUEIA com mensagem explicando o fix.
- Metadata honesta: spam de pontuação no título e links de afiliado sem
  divulgação viram sugestões (FTC + política de spam do YouTube).
"""
from __future__ import annotations

import asyncio
import os
import re

from backend.agents.base_agent import BaseAgent
from backend.config import ROOT_DIR

BANNED_FILE = ROOT_DIR / "backend" / "rules" / "ig_banned_hashtags.txt"

FINANCIAL_PROMISES = re.compile(
    r"\b(ganhe?\s+dinheiro\b|ganhe?\s+r\$|fique\s+rico\b|renda\s+garantida\b|lucro\s+garantido\b|"
    r"dinheiro\s+f[áa]cil\b|enriquecer\s+r[áa]pido\b|get\s+rich\b)",
    re.IGNORECASE,
)

AFFILIATE_LINK = re.compile(r"https?://", re.IGNORECASE)
AFFILIATE_DISCLOSURE = re.compile(
    r"afiliad|comiss[ãa]o|parceria|patroc[ií]nio|publieditorial|#\s*ad\b|\(ads?\)",
    re.IGNORECASE,
)
TITLE_SPAM_PUNCT = re.compile(r"[!?]")


class ComplianceAgent(BaseAgent):
    name = "compliance_agent"

    async def run(
        self,
        seo: dict | None = None,
        main_video: dict | None = None,
        shorts: list | None = None,
        target_platforms: list | None = None,
        **_,
    ) -> dict:
        seo = seo or self.ctx_get("seo") or {}
        main_video = main_video or self.ctx_get("main_video") or {}
        shorts = shorts if shorts is not None else (self.ctx_get("shorts") or [])
        target_platforms = target_platforms or self.ctx_get("target_platforms") or ["youtube", "tiktok", "instagram"]
        # Sinais de origem para as regras de IA/conteúdo reutilizado: vídeo do
        # Drive carrega source=drive_ready_video no ctx; vídeo IA tem roteiro
        # com cenas + narração TTS no ctx.
        policy_ctx = {
            "source": self.ctx_get("source"),
            "ai_narrated": bool((self.ctx_get("script") or {}).get("scenes")),
        }

        report = await asyncio.to_thread(self._check, seo, main_video, shorts, target_platforms, policy_ctx)
        self.ctx_set("compliance", report)
        self.emit("progress", f"Compliance: {report['status']}", progress=90, compliance=report)
        return report

    def _check(self, seo, main_video, shorts, platforms, policy=None) -> dict:
        policy = policy or {}
        blocks: list[str] = []
        suggestions: list[str] = []
        flags: list[str] = []

        if "youtube" in platforms:
            yt = seo.get("youtube", {})
            title = yt.get("title", "") or ""
            words = title.split()
            caps = [w for w in words if len(w) > 2 and w.isupper()]
            if words and len(caps) / len(words) > 0.5:
                suggestions.append("YouTube: título com muitas palavras em CAIXA ALTA")
            if FINANCIAL_PROMISES.search(title) or FINANCIAL_PROMISES.search(yt.get("description", "")):
                blocks.append("YouTube: promessa de ganhos financeiros não permitida")
            if not yt.get("tags"):
                suggestions.append("YouTube: sem tags relacionadas ao conteúdo")
            desc = yt.get("description", "") or ""
            if len(TITLE_SPAM_PUNCT.findall(title)) > 3:
                suggestions.append("YouTube: título com pontuação excessiva (!!!/???) — cara de spam, derruba CTR")
            if AFFILIATE_LINK.search(desc) and not AFFILIATE_DISCLOSURE.search(desc):
                suggestions.append("YouTube: descrição com links sem frase de divulgação (afiliado/patrocínio) — exigência FTC/YouTube, adicione ex.: '(links afiliados)'")
            # --- Política de IA + conteúdo reutilizado (YPP) ---
            ai_synthetic = bool(policy.get("ai_narrated"))
            drive_source = policy.get("source") == "drive_ready_video"
            if ai_synthetic or drive_source:
                # A API não marca sozinha: o operador marca no Studio. Flag de
                # máquina para a UI + sugestão legível para o humano.
                flags.append("ai_disclosure_required")
                suggestions.append("YouTube: conteúdo com IA (narração/imagens) — marque 'conteúdo alterado' no YouTube Studio após publicar")
            if drive_source:
                curated = os.path.basename(
                    (main_video or {}).get("main_video_path") or "").startswith("curated_")
                if not curated:
                    blocks.append("YouTube: vídeo do Drive SEM curadoria (re-upload puro, sem comentário próprio) — padrão que o YouTube desmonetiza como 'conteúdo reutilizado'. Regenere com a curadoria ligada ou revise manualmente")
                else:
                    suggestions.append("YouTube: vídeo do Drive com curadoria — confira se o comentário próprio está audível (proteção reused content)")

        if "tiktok" in platforms:
            tk = seo.get("tiktok", {})
            cap = tk.get("caption", "") or ""
            if len(cap) > 150:
                blocks.append(f"TikTok: caption com {len(cap)} chars (máx 150)")
            if cap.count("#") > 30:
                blocks.append("TikTok: mais de 30 hashtags")
            # Shorts length must be within TikTok limits (<=3min for most accounts).
            for s in shorts or []:
                if s.get("length", 0) > 180:
                    suggestions.append(f"TikTok: short {s.get('num')} acima de 3min")

        if "instagram" in platforms:
            ig = seo.get("instagram", {})
            cap = ig.get("caption", "") or ""
            if len(cap) > 2200:
                blocks.append(f"Instagram: caption com {len(cap)} chars (máx 2200)")
            banned = self._banned()
            hit = [h for h in ig.get("hashtags", []) if h.lstrip("#").lower() in banned]
            if hit:
                blocks.append(f"Instagram: hashtags restritas: {', '.join(hit)}")

        status = "blocked" if blocks else "approved"
        # Risk score determinístico: cada bloqueio pesa 40, cada sugestão 10.
        # blocked => no máximo 60 (há pelo menos 1 block); sem nada => 100.
        risk = max(0, 100 - 40 * len(blocks) - 10 * len(suggestions))
        return {"status": status, "blocks": blocks, "suggestions": suggestions,
                "flags": flags, "risk_score": risk}

    @staticmethod
    def _banned() -> set[str]:
        if not BANNED_FILE.exists():
            return set()
        out = set()
        for line in BANNED_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                out.add(line.lower())
        return out


if __name__ == "__main__":
    async def _demo():
        seo = {
            "youtube": {"title": "GANHE DINHEIRO FÁCIL AGORA", "description": "", "tags": []},
            "tiktok": {"caption": "ok #a #b #fyp"},
            "instagram": {"caption": "hi", "hashtags": ["#alone", "#viral"]},
        }
        agent = ComplianceAgent(job_id=0, emit=False)
        print(await agent.execute(seo=seo, shorts=[], target_platforms=["youtube", "tiktok", "instagram"]))

    asyncio.run(_demo())
