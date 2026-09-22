"""AI Copilot — responde perguntas sobre o próprio sistema usando APENAS
dados reais do banco (jobs, analytics, saúde). Nenhuma afirmação é inventada:
cada resposta carrega os fatos (facts) que a sustentam e ações (actions) que
levam o operador direto à página relevante.

O parsing de intenção é determinístico (pt-BR) — sem custo de LLM e com
resposta instantânea. Se a pergunta não casar com nenhuma intenção conhecida,
cai num overview geral com sugestões do que perguntar.
"""
from __future__ import annotations

import logging
import re
import unicodedata
from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from backend.database import get_db
from backend.error_messages import friendly_error
from backend.models import JobStatus, PlatformAccount, VideoJob

logger = logging.getLogger("studio.copilot")

router = APIRouter(prefix="/copilot", tags=["copilot"])


class AskRequest(BaseModel):
    question: str = Field(..., min_length=2, max_length=500)
    account_id: int | None = None


class AskResponse(BaseModel):
    intent: str
    answer: str
    facts: list[str] = []
    actions: list[dict] = []
    # True quando a resposta foi reescrita por LLM em linguagem natural; False
    # = resposta determinística pura (sempre disponível, mesmo sem chave de IA).
    enhanced: bool = False


def _norm(text: str) -> str:
    """Minúsculas sem acento — casamento de intenção tolerante a acentuação."""
    return unicodedata.normalize("NFKD", text.lower()).encode("ascii", "ignore").decode()


def _detect_intent(q: str) -> str:
    q = _norm(q)
    # "job 42" / "#42" com palavra de etapa/estado → pergunta sobre UM job específico.
    if re.search(r"(?:job|video|#)\s*#?\d+", q) and re.search(
            r"\b(etapa|fase|status|onde|parou|falhou|aconteceu)\b", q):
        return "job_detail"
    rules = [
        ("automations", r"automac|\bregras?\b"),
        ("pending_approvals", r"\b(aprova|aguardando|pendente|revisar)\b"),
        ("failures", r"\b(falh|erro|quebr|travad|por ?que)\b"),
        ("best_format", (r"\b(formato|tipo|estilo|categoria)\b.*\b(melhor|funciona|performa|resultado)\b"
                         r"|\bmelhor\b.*\b(formato|tipo|conteudo)\b")),
        ("best_videos", (r"\b(melhor|top|mais)\b.*\b(desempenho|views|visualiz|video|conteudo)\b"
                         r"|\bo que (esta|ta) funcionando\b")),
        ("publish_today", r"\bpublica(r|o)?\b.*\bhoje\b|\bhoje\b.*\bpublica|\bo que devo\b"),
        ("queue_status", r"\b(fila|andamento|processando|rodando|ativos?|status do)\b"),
        ("system_health", (r"\b(saude|health|offline|online|api|sistema)\b.*"
                           r"\b(ok|saude|health|status|caiu|offline|funcionando)\b"
                           r"|\bsaude do sistema\b|\bapis?\b")),
    ]
    for intent, pattern in rules:
        if re.search(pattern, q):
            return intent
    return "overview"


def _job_brief(job: VideoJob) -> str:
    fmt = "Short" if job.video_format == "short" else "Longo"
    return f"#{job.id} · {job.title[:60]} ({fmt})"


def _status_counts(db: Session) -> dict[str, int]:
    rows = db.execute(
        select(VideoJob.status, func.count()).group_by(VideoJob.status)
    ).all()
    return {(s.value if hasattr(s, "value") else s): c for s, c in rows}


def _extract_job_id(q: str) -> int | None:
    m = re.search(r"(?:job|video|#)\s*#?(\d+)", _norm(q))
    return int(m.group(1)) if m else None


def _answer_automations(db: Session) -> AskResponse:
    from backend.models import AutomationRule

    rules = db.execute(select(AutomationRule).order_by(AutomationRule.id)).scalars().all()
    if not rules:
        return AskResponse(
            intent="automations",
            answer="Nenhuma regra de automação cadastrada. Crie uma na página Automações.",
            actions=[{"label": "Abrir Automações", "to": "/rules"}],
        )
    today = datetime.utcnow().date()
    ran_today = [r for r in rules if r.last_run_at and r.last_run_at.date() == today]
    lines = [f"Há **{len(rules)}** regra(s) de automação ({sum(1 for r in rules if r.enabled)} ativas):"]
    for r in rules:
        state = "ativa" if r.enabled else "inativa"
        last = r.last_run_at.strftime("%d/%m %H:%M") if r.last_run_at else "nunca"
        lines.append(f"• **{r.name}** ({state}) — executada {r.runs}× · última: {last}")
    if ran_today:
        lines.append(f"\nHoje já rodaram: {', '.join(r.name for r in ran_today)}.")
    else:
        lines.append("\nNenhuma regra rodou hoje ainda.")
    return AskResponse(
        intent="automations",
        answer="\n".join(lines),
        facts=[f"{r.name}: {r.runs}x" for r in rules],
        actions=[{"label": "Abrir Automações", "to": "/rules"}],
    )


def _answer_job_detail(db: Session, question: str) -> AskResponse:
    job_id = _extract_job_id(question)
    job = db.get(VideoJob, job_id) if job_id else None
    if not job:
        return AskResponse(
            intent="job_detail",
            answer=f"Não encontrei o job #{job_id}. Confira o número na Fila.",
            actions=[{"label": "Abrir fila", "to": "/queue"}],
        )
    status = job.status.value if hasattr(job.status, "value") else job.status
    label = {
        "queued": "na fila, aguardando início", "processing": "em produção agora",
        "awaiting_approval": "pronto, aguardando sua aprovação",
        "approved": "aprovado, aguardando publicação", "publishing": "publicando",
        "published": "publicado", "error": "com erro", "rejected": "rejeitado",
        "awaiting_quota": "aguardando reset de quota",
        "tiktok_pending_approval": "em revisão no TikTok",
    }.get(status, status)
    lines = [f"**Job #{job.id}** — {job.title[:70]}", f"Status: **{label}**."]
    if status == "processing":
        lines.append(f"Etapa atual: `{job.current_agent or '-'}` ({job.progress}%). "
                     "Abra a Fila e toque em ⏱ para ver a linha do tempo completa.")
    if job.error_message:
        why = friendly_error(job.error_message) or job.error_message
        lines.append(f"Motivo do erro: {why}")
    if job.qc_status:
        lines.append(f"QC: {job.qc_status} · Compliance: {job.compliance_status or '—'}")
    facts = [f"job {job.id}: {status}", f"agent={job.current_agent}", f"progress={job.progress}"]
    actions = [{"label": "Ver na fila", "to": "/queue"}]
    if status == "awaiting_approval":
        actions = [{"label": "Revisar agora", "to": "/approvals"}]
    return AskResponse(intent="job_detail", answer="\n".join(lines), facts=facts, actions=actions)


# ---------------------------------------------------------------- intents ---

def _answer_pending_approvals(db: Session) -> AskResponse:
    jobs = db.execute(
        select(VideoJob)
        .where(VideoJob.status == JobStatus.AWAITING_APPROVAL)
        .order_by(VideoJob.created_at.desc())
        .limit(10)
    ).scalars().all()
    if not jobs:
        return AskResponse(
            intent="pending_approvals",
            answer="Nenhum vídeo aguardando aprovação no momento — a fila de revisão está limpa.",
            actions=[{"label": "Abrir Aprovações", "to": "/approvals"}],
        )
    lines = [f"Há **{len(jobs)}** vídeo(s) aguardando sua aprovação:"]
    facts = []
    for j in jobs:
        qc = (j.qc_status or "sem QC").replace("qc_", "")
        lines.append(f"• {_job_brief(j)} — QC: {qc}")
        facts.append(f"job {j.id} aguardando, qc={j.qc_status}")
    return AskResponse(
        intent="pending_approvals",
        answer="\n".join(lines),
        facts=facts,
        actions=[{"label": "Revisar agora", "to": "/approvals"}],
    )


def _answer_failures(db: Session) -> AskResponse:
    jobs = db.execute(
        select(VideoJob)
        .where(VideoJob.status == JobStatus.ERROR)
        .order_by(VideoJob.updated_at.desc())
        .limit(8)
    ).scalars().all()
    if not jobs:
        return AskResponse(
            intent="failures",
            answer="Nenhum job em erro no momento. O sistema está sem falhas pendentes.",
            actions=[{"label": "Ver fila", "to": "/queue"}],
        )
    lines = [f"Encontrei **{len(jobs)}** job(s) em erro (mais recentes):"]
    facts = []
    for j in jobs:
        why = friendly_error(j.error_message) or j.error_message or "erro não registrado"
        stage = j.current_agent or "desconhecida"
        lines.append(f"• {_job_brief(j)} — etapa `{stage}`: {why}")
        facts.append(f"job {j.id} erro em {stage}: {str(j.error_message)[:120]}")
    lines.append("\nVocê pode usar **Retry** na fila ou **Corrigir sistema** nas Configurações.")
    return AskResponse(
        intent="failures",
        answer="\n".join(lines),
        facts=facts,
        actions=[{"label": "Abrir fila", "to": "/queue"}, {"label": "Corrigir sistema", "to": "/settings"}],
    )


def _answer_best_format(db: Session, account_id: int | None) -> AskResponse:
    from backend.agents.performance import PerformanceInsights

    # Sem conta escolhida, usa a conta com mais vídeos medidos.
    if account_id is None:
        row = db.execute(
            select(VideoJob.account_id, func.count())
            .where(VideoJob.account_id.isnot(None))
            .group_by(VideoJob.account_id)
            .order_by(func.count().desc())
        ).first()
        account_id = row[0] if row else None
    ins = PerformanceInsights(db).account_insights(account_id)
    if not ins.get("ready"):
        return AskResponse(
            intent="best_format",
            answer=(
                f"Ainda não há dados suficientes para concluir — só {ins.get('n', 0)} vídeo(s) "
                "com métricas reais coletadas (mínimo 3). Conforme os vídeos publicados "
                "acumularem views, esta resposta passa a rankear os formatos de verdade."
            ),
            actions=[{"label": "Ver Analytics", "to": "/analytics"}],
        )
    ranking = ins.get("type_ranking") or []
    lines = [f"Com base em **{ins['n']}** vídeos com métricas reais deste canal:"]
    for t in ranking[:4]:
        lines.append(f"• `{t['type']}` — média de **{t['avg_views']}** views ({t['n']} vídeo(s))")
    if ins.get("best_type"):
        lines.append(f"\nMelhor formato até agora: **{ins['best_type']}**. "
                     "Recomendo priorizar esse padrão nos próximos vídeos.")
    return AskResponse(
        intent="best_format",
        answer="\n".join(lines),
        facts=[f"{t['type']}: {t['avg_views']} views médios" for t in ranking],
        actions=[{"label": "Abrir Analytics", "to": "/analytics"}],
    )


def _answer_best_videos(db: Session, account_id: int | None) -> AskResponse:
    q = (
        select(VideoJob)
        .options(selectinload(VideoJob.analytics))
        .where(VideoJob.status == JobStatus.PUBLISHED)
        .order_by(VideoJob.updated_at.desc())
        .limit(100)
    )
    if account_id:
        q = q.where(VideoJob.account_id == account_id)
    jobs = db.execute(q).scalars().all()
    scored = []
    for j in jobs:
        latest: dict[str, object] = {}
        for r in (j.analytics or []):
            cur = latest.get(r.platform)
            if cur is None or (r.collected_at and (cur.collected_at is None or r.collected_at > cur.collected_at)):
                latest[r.platform] = r
        views = sum(int(getattr(r, "views", 0) or 0) for r in latest.values())
        if views > 0:
            scored.append((views, j))
    if not scored:
        return AskResponse(
            intent="best_videos",
            answer="Ainda não há vídeos publicados com views medidas. Assim que as métricas "
                   "forem coletadas (2h/24h/7d após publicar), listo os melhores aqui.",
            actions=[{"label": "Ver Analytics", "to": "/analytics"}],
        )
    scored.sort(key=lambda x: x[0], reverse=True)
    top = scored[:5]
    lines = ["Top vídeos por **views reais**:"]
    for views, j in top:
        lines.append(f"• {_job_brief(j)} — **{views}** views")
    return AskResponse(
        intent="best_videos",
        answer="\n".join(lines),
        facts=[f"job {j.id}: {v} views" for v, j in top],
        actions=[{"label": "Abrir Analytics", "to": "/analytics"}],
    )


def _answer_publish_today(db: Session) -> AskResponse:
    now = datetime.utcnow()
    eod = now.replace(hour=23, minute=59, second=59)
    ready = db.execute(
        select(VideoJob)
        .where(VideoJob.status == JobStatus.APPROVED)
        .where((VideoJob.scheduled_at.is_(None)) | (VideoJob.scheduled_at <= eod))
        .order_by(VideoJob.scheduled_at.asc().nullsfirst())
        .limit(10)
    ).scalars().all()
    awaiting = db.execute(
        select(func.count()).select_from(VideoJob)
        .where(VideoJob.status == JobStatus.AWAITING_APPROVAL)
    ).scalar_one()
    lines: list[str] = []
    facts: list[str] = []
    if ready:
        lines.append(f"**{len(ready)}** vídeo(s) aprovados e prontos para publicar hoje:")
        for j in ready:
            when = j.scheduled_at.strftime("%H:%M") if j.scheduled_at else "sem horário"
            lines.append(f"• {_job_brief(j)} — {when}")
            facts.append(f"job {j.id} aprovado, slot={when}")
    else:
        lines.append("Nenhum vídeo aprovado aguardando publicação hoje.")
    if awaiting:
        lines.append(f"\nAlém disso, **{awaiting}** vídeo(s) aguardam sua aprovação — "
                     "aprová-los libera mais publicações para hoje.")
    return AskResponse(
        intent="publish_today",
        answer="\n".join(lines),
        facts=facts,
        actions=[{"label": "Abrir Agenda", "to": "/schedule"}, {"label": "Aprovações", "to": "/approvals"}],
    )


def _answer_queue_status(db: Session) -> AskResponse:
    counts = _status_counts(db)
    total = sum(counts.values())
    running = db.execute(
        select(VideoJob)
        .where(VideoJob.status == JobStatus.PROCESSING)
        .order_by(VideoJob.updated_at.desc())
        .limit(5)
    ).scalars().all()
    if total == 0:
        return AskResponse(
            intent="queue_status",
            answer="O sistema ainda não tem nenhum job. Crie o primeiro vídeo em '+ Novo vídeo'.",
            actions=[{"label": "Abrir Dashboard", "to": "/"}],
        )
    lines = [f"Resumo da fila (**{total}** jobs no total):"]
    label = {
        "queued": "na fila", "processing": "processando", "awaiting_approval": "aguardando aprovação",
        "approved": "aprovados", "publishing": "publicando", "published": "publicados",
        "error": "com erro", "rejected": "rejeitados", "awaiting_quota": "aguardando cota",
        "tiktok_pending_approval": "TikTok em revisão",
    }
    for status, n in sorted(counts.items(), key=lambda x: x[1], reverse=True):
        lines.append(f"• **{n}** {label.get(status, status)}")
    for j in running:
        lines.append(f"\nEm produção agora: {_job_brief(j)} — etapa `{j.current_agent or '-'}` ({j.progress}%)")
    return AskResponse(
        intent="queue_status",
        answer="\n".join(lines),
        facts=[f"{k}: {v}" for k, v in counts.items()],
        actions=[{"label": "Abrir fila", "to": "/queue"}],
    )


def _answer_system_health() -> AskResponse:
    from backend.agents.error_recovery import get_system_health

    health = get_system_health()
    checks = health.get("checks", {})
    bad = {k: v for k, v in checks.items() if v.get("status") != "green"}
    if not bad:
        return AskResponse(
            intent="system_health",
            answer=f"Todos os {len(checks)} componentes estão verdes: banco, scheduler, disco, "
                   "worker, FFmpeg e provedores de IA operando normalmente.",
            facts=[f"{k}: green" for k in checks],
            actions=[{"label": "Ver detalhes", "to": "/system"}],
        )
    lines = ["Alguns componentes precisam de atenção:"]
    for name, c in bad.items():
        lines.append(f"• `{name}` — {c.get('status')}: {c.get('detail', '')}")
    return AskResponse(
        intent="system_health",
        answer="\n".join(lines),
        facts=[f"{k}: {v.get('status')}" for k, v in checks.items()],
        actions=[{"label": "Abrir Saúde do Sistema", "to": "/system"},
                 {"label": "Corrigir sistema", "to": "/settings"}],
    )


def _answer_overview(db: Session) -> AskResponse:
    counts = _status_counts(db)
    accounts = db.execute(select(func.count()).select_from(PlatformAccount)).scalar_one()
    awaiting = counts.get("awaiting_approval", 0)
    errors = counts.get("error", 0)
    published = counts.get("published", 0)
    answer = (
        f"Visão geral: **{published}** vídeos publicados, **{awaiting}** aguardando aprovação, "
        f"**{errors}** com erro, em **{accounts}** conta(s) conectada(s).\n\n"
        "Posso responder com dados reais, por exemplo:\n"
        "• *Quais vídeos estão aguardando aprovação?*\n"
        "• *Por que esse job falhou?*\n"
        "• *Qual formato está funcionando melhor?*\n"
        "• *Quais vídeos devo publicar hoje?*\n"
        "• *Qual conteúdo teve melhor desempenho?*\n"
        "• *Em que etapa está o job 12?*\n"
        "• *Quais automações rodaram hoje?*\n"
        "• *Como está a saúde do sistema?*"
    )
    return AskResponse(intent="overview", answer=answer)


# ----------------------------------------------------- polimento via LLM ---
# A resposta determinística é sempre a fonte da verdade (fatos reais do banco).
# Quando há provedor LLM configurado, reescrevemos essa MESMA resposta em
# linguagem natural — o prompt proíbe explicitamente inventar números e manda
# manter os fatos. Qualquer falha (sem chave, 429, timeout, resposta vazia)
# devolve o texto determinístico original, silenciosamente: o operador nunca
# fica sem resposta por causa do LLM.

_POLISH_TIMEOUT_S = 8.0


def _maybe_polish(resp: AskResponse, question: str) -> AskResponse:
    """Reescreve a resposta com LLM quando disponível; senão retorna como está."""
    # Overview já é um menu de opções — polir só adicionaria latência.
    if resp.intent == "overview":
        return resp
    try:
        from backend import llm
    except Exception:  # noqa: BLE001
        return resp
    if not llm.available():
        return resp
    import asyncio

    system = (
        "Você é o copiloto de um sistema de automação de vídeos. Reescreva a resposta "
        "abaixo em português natural, claro e direto, como um assistente conversando. "
        "REGRAS INEGOCIÁVEIS: use APENAS os números, nomes, títulos e status que já "
        "estão na resposta — não invente, não arredonde, não adicione dados novos, não "
        "remova nenhum fato. Preserve marcações **negrito** e os títulos dos vídeos. "
        "Responda só com o texto final, sem preâmbulo."
    )
    prompt = f"PERGUNTA DO USUÁRIO: {question}\n\nRESPOSTA (fatos reais):\n{resp.answer}"
    try:
        text = asyncio.run(asyncio.wait_for(
            llm.complete(prompt, system=system, max_tokens=700, fast=True),
            timeout=_POLISH_TIMEOUT_S,
        ))
    except Exception:  # noqa: BLE001 — sem LLM, a resposta determinística já serve
        return resp
    if not text or len(text.strip()) < 20:
        return resp
    # Segurança: se o LLM derrubou um número inteiro que estava na resposta
    # original (ex.: "3" vídeos), a reescrita não é confiável — usa a original.
    import re as _re
    original_numbers = set(_re.findall(r"\d+", resp.answer))
    polished_numbers = set(_re.findall(r"\d+", text))
    if not original_numbers.issubset(polished_numbers):
        return resp
    resp.answer = text.strip()
    resp.enhanced = True
    return resp


@router.post("/ask", response_model=AskResponse)
def ask(payload: AskRequest, db: Session = Depends(get_db)):
    intent = _detect_intent(payload.question)
    try:
        if intent == "automations":
            resp = _answer_automations(db)
        elif intent == "job_detail":
            resp = _answer_job_detail(db, payload.question)
        elif intent == "pending_approvals":
            resp = _answer_pending_approvals(db)
        elif intent == "failures":
            resp = _answer_failures(db)
        elif intent == "best_format":
            resp = _answer_best_format(db, payload.account_id)
        elif intent == "best_videos":
            resp = _answer_best_videos(db, payload.account_id)
        elif intent == "publish_today":
            resp = _answer_publish_today(db)
        elif intent == "queue_status":
            resp = _answer_queue_status(db)
        elif intent == "system_health":
            resp = _answer_system_health()
        else:
            resp = _answer_overview(db)
        return _maybe_polish(resp, payload.question)
    except Exception as exc:  # noqa: BLE001 — o copilot nunca derruba a API
        logger.exception("copilot ask falhou (intent=%s)", intent)
        return AskResponse(
            intent=intent,
            answer=f"Não consegui consultar essa informação agora ({exc}). "
                   "Tente novamente em instantes.",
        )


@router.get("/suggestions")
def suggestions(db: Session = Depends(get_db)):
    """Perguntas sugeridas contextuais — derivadas do estado REAL do sistema
    (não mostra 'quem falhou?' se não há falha nenhuma)."""
    out: list[str] = []
    counts = _status_counts(db)
    if counts.get("awaiting_approval"):
        out.append("Quais vídeos estão aguardando aprovação?")
    if counts.get("error"):
        out.append("Por que os jobs falharam?")
    if counts.get("approved"):
        out.append("Quais vídeos devo publicar hoje?")
    if counts.get("published"):
        out.append("Qual conteúdo teve melhor desempenho?")
        out.append("Qual formato está funcionando melhor?")
    out.append("Como está a fila agora?")
    out.append("Quais automações rodaram hoje?")
    out.append("Como está a saúde do sistema?")
    return {"suggestions": out[:6]}
