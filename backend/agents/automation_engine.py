"""AutomationEngine — executa as regras SE->ENTÃO (AutomationRule) nos gatilhos
reais do pipeline.

Gatilhos (disparados pelo orquestrador/publisher/rotas):
  job_awaiting_approval  vídeo terminou e espera revisão humana
  job_approved           vídeo foi aprovado (humano ou auto)
  job_error              pipeline falhou de vez (retries do agente esgotados)
  job_published          publicação concluída em ao menos 1 plataforma

Ações (todas reais — nenhuma simulada):
  notify             empurra um evento WS "notification" -> sino do TopBar
  retry_job          reenfileira um job em ERRO (com teto de tentativas e
                     bloqueio dos marcadores de risco de duplicata)
  collect_analytics  agenda a coleta de métricas do vídeo publicado

Segurança anti-loop: retry_job só age se o job ainda estiver em ERROR e
retry_count < config.max_attempts (default 2) — e NUNCA toca em jobs cujo erro
indica risco de upload duplicado (mesmos marcadores que o scheduler respeita).

evaluate() nunca levanta exceção: uma regra quebrada não pode derrubar o
pipeline que a disparou.
"""
from __future__ import annotations

import logging
from datetime import datetime

from sqlalchemy import select

from backend.database import SessionLocal
from backend.events import publish_event
from backend.models import AutomationRule, JobStatus, VideoJob

logger = logging.getLogger("studio.automation")

TRIGGERS = {
    "job_awaiting_approval": "Quando um vídeo ficar pronto para aprovação",
    "job_approved": "Quando um vídeo for aprovado",
    "job_error": "Quando um job falhar",
    "job_published": "Quando um vídeo for publicado",
}

ACTIONS = {
    "notify": "Enviar notificação",
    "retry_job": "Tentar novamente automaticamente",
    "collect_analytics": "Coletar métricas (2h/24h/7d)",
}

# Regras padrão criadas na primeira execução — o sistema nasce útil, e o
# operador pode desligar/editar cada uma na página Automações.
DEFAULT_RULES = [
    {"name": "Avisar quando houver vídeo para aprovar",
     "trigger": "job_awaiting_approval", "action": "notify", "config": {}},
    {"name": "Retentar jobs que falharam (máx. 2x)",
     "trigger": "job_error", "action": "retry_job", "config": {"max_attempts": 2}},
    {"name": "Coletar métricas após publicar",
     "trigger": "job_published", "action": "collect_analytics", "config": {}},
]

# Mesmos marcadores de backend/routers/jobs.py — um erro que indica "o vídeo
# PODE já estar no canal" nunca é reprocessado automaticamente.
_DUPLICATE_RISK_MARKERS = (
    "PODE já estar no canal",
    "Verifique o YouTube",
    "Render interrompido",
)


def ensure_default_rules() -> None:
    """Semeia as regras padrão uma única vez (idempotente: só se não houver
    nenhuma regra ainda)."""
    db = SessionLocal()
    try:
        if db.execute(select(AutomationRule).limit(1)).first():
            return
        for spec in DEFAULT_RULES:
            db.add(AutomationRule(**spec))
        db.commit()
        logger.info("Regras de automação padrão criadas (%d).", len(DEFAULT_RULES))
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        logger.warning("ensure_default_rules falhou: %s", exc)
    finally:
        db.close()


def _notify(rule: AutomationRule, job: VideoJob) -> str:
    messages = {
        "job_awaiting_approval": f"Vídeo #{job.id} finalizado e aguardando aprovação.",
        "job_approved": f"Vídeo #{job.id} aprovado — entrando na publicação.",
        "job_error": f"Job #{job.id} falhou: {(job.error_message or 'erro')[:120]}",
        "job_published": f"Vídeo #{job.id} publicado com sucesso.",
    }
    categories = {"job_error": "error", "job_published": "success",
                  "job_awaiting_approval": "action", "job_approved": "info"}
    publish_event({
        "type": "notification",
        "category": categories.get(rule.trigger, "info"),
        "message": messages.get(rule.trigger, f"Evento {rule.trigger} no job #{job.id}."),
        "job_id": job.id,
        "to": "/approvals" if rule.trigger == "job_awaiting_approval" else "/queue",
        "rule": rule.name,
    })
    return "notificação enviada"


def _retry_job(rule: AutomationRule, job: VideoJob, db) -> str:
    if job.status != JobStatus.ERROR:
        return f"ignorado (status={job.status.value}, não está em erro)"
    msg = job.error_message or ""
    if any(m in msg for m in _DUPLICATE_RISK_MARKERS):
        return "ignorado (risco de duplicar publicação — retry manual apenas)"
    max_attempts = int((rule.config or {}).get("max_attempts", 2))
    if (job.retry_count or 0) >= max_attempts:
        return f"ignorado (teto de {max_attempts} tentativas atingido)"
    from backend.pipeline.dispatch import dispatch_job

    job.status = JobStatus.QUEUED
    job.error_message = None
    job.progress = 0
    job.retry_count = (job.retry_count or 0) + 1
    db.commit()
    dispatch_job(job.id)
    publish_event({
        "type": "notification", "category": "alert",
        "message": f"Regra '{rule.name}': job #{job.id} reenfileirado "
                   f"(tentativa {job.retry_count}/{max_attempts}).",
        "job_id": job.id, "to": "/queue", "rule": rule.name,
    })
    return f"reenfileirado (tentativa {job.retry_count}/{max_attempts})"


def _collect_analytics(rule: AutomationRule, job: VideoJob, db) -> str:
    from backend.agents.analytics import AnalyticsAgent

    # Coleta "2h" imediata (sync, mesma sessão) — o scheduler agenda 24h/7d.
    collected = AnalyticsAgent(db).collect_for_job(job.id, snapshot_type="2h")
    db.commit()
    if not collected:
        return "ignorado (sem plataforma publicada com video_id para medir)"
    return f"métricas coletadas ({len(collected)} plataforma(s))"


_ACTION_FNS = {
    "notify": lambda rule, job, db: _notify(rule, job),
    "retry_job": _retry_job,
    "collect_analytics": _collect_analytics,
}


def evaluate(trigger: str, job_id: int) -> list[dict]:
    """Executa toda regra habilitada para `trigger` sobre o job. Retorna um
    relatório por regra; nunca levanta exceção."""
    results: list[dict] = []
    db = SessionLocal()
    try:
        rules = db.execute(
            select(AutomationRule)
            .where(AutomationRule.trigger == trigger, AutomationRule.enabled.is_(True))
        ).scalars().all()
        if not rules:
            return results
        job = db.get(VideoJob, job_id)
        if not job:
            return results
        for rule in rules:
            fn = _ACTION_FNS.get(rule.action)
            if fn is None:
                continue
            try:
                outcome = fn(rule, job, db)
                rule.runs = (rule.runs or 0) + 1
                rule.last_run_at = datetime.utcnow()
                db.commit()
                results.append({"rule": rule.name, "outcome": outcome})
                logger.info("automação [%s] regra '%s' job %s: %s",
                            trigger, rule.name, job_id, outcome)
            except Exception as exc:  # noqa: BLE001 — uma regra não derruba as outras
                db.rollback()
                logger.warning("automação regra '%s' falhou (job %s): %s", rule.name, job_id, exc)
                results.append({"rule": rule.name, "outcome": f"erro: {exc}"})
    except Exception as exc:  # noqa: BLE001 — evaluate nunca derruba o chamador
        logger.warning("evaluate(%s, job %s) falhou: %s", trigger, job_id, exc)
    finally:
        db.close()
    return results
