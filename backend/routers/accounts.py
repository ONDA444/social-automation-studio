"""Accounts API + OAuth connect flows for YouTube / TikTok / Instagram."""
from __future__ import annotations

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from backend.database import get_db
from backend.agents.account_profile import AccountProfileService
from backend.agents.drive_library import extract_folder_id
from backend.uploaders import instagram as ig
from backend.uploaders import tiktok as tk
from backend.uploaders import youtube as yt

router = APIRouter(tags=["accounts"])

PLATFORMS = {"youtube": yt, "tiktok": tk, "instagram": ig}


class AccountCreate(BaseModel):
    platform: str
    display_name: str = Field(..., min_length=1)
    niche: str | None = None
    target_audience: str | None = None
    preferred_voice: str = "pt-BR-AntonioNeural"
    content_tone: str = "neutral"
    content_language: str = "pt-BR"
    preferred_templates: list[str] = Field(default_factory=list)


class AccountUpdate(BaseModel):
    display_name: str | None = None
    niche: str | None = None
    target_audience: str | None = None
    preferred_voice: str | None = None
    content_tone: str | None = None
    content_language: str | None = None
    music_style: str | None = None  # calm | balanced | energetic
    video_source_mode: str | None = None  # ai | drive | mixed
    drive_folder_id: str | None = None
    drive_folder_url: str | None = None
    drive_niche: str | None = None
    drive_recursive: bool | None = None
    ride_trends: bool | None = None          # 🔥 Momento em alta (opt-in)
    trends_per_cycle: int | None = None      # 1..2
    preferred_templates: list[str] | None = None
    avoid_topics: list[str] | None = None
    schedule: dict | None = None


class LinkRequest(BaseModel):
    links: dict                      # {"tiktok": id, "instagram": id}
    mirror_to_linked: bool = True
    mirror_format: str = "shorts_30s"


# ---------------------------------------------------------------- CRUD
@router.get("/accounts")
def list_accounts(platform: str | None = Query(None), db: Session = Depends(get_db)):
    svc = AccountProfileService(db)
    return {"accounts": [a.to_dict() for a in svc.list(platform)]}


@router.post("/accounts")
def create_account(payload: AccountCreate, db: Session = Depends(get_db)):
    if payload.platform not in PLATFORMS:
        raise HTTPException(400, f"plataforma inválida: {payload.platform}")
    svc = AccountProfileService(db)
    acct = svc.create(**payload.model_dump())
    return acct.to_dict()


@router.get("/accounts/{account_id}")
def get_account(account_id: int, db: Session = Depends(get_db)):
    acct = AccountProfileService(db).get(account_id)
    if not acct:
        raise HTTPException(404, "conta não encontrada")
    return acct.to_dict()


@router.patch("/accounts/{account_id}")
def update_account(account_id: int, payload: AccountUpdate, db: Session = Depends(get_db)):
    svc = AccountProfileService(db)
    acct = svc.get(account_id)
    if not acct:
        raise HTTPException(404, "conta não encontrada")
    data = payload.model_dump(exclude_none=True)
    for k, v in data.items():
        if k == "trends_per_cycle":
            v = max(1, min(2, int(v)))  # never let a channel flood: cap at 2/cycle
        if k == "video_source_mode":
            v = v if v in {"ai", "drive", "mixed"} else "ai"
        setattr(acct, k, v)
    if "drive_folder_url" in data:
        acct.drive_folder_id = extract_folder_id(acct.drive_folder_url)
    db.commit()
    db.refresh(acct)
    return acct.to_dict()


@router.delete("/accounts/{account_id}")
def delete_account(account_id: int, db: Session = Depends(get_db)):
    svc = AccountProfileService(db)
    acct = svc.get(account_id)
    if not acct:
        raise HTTPException(404, "conta não encontrada")
    db.delete(acct)
    db.commit()
    return {"deleted": account_id}


@router.post("/accounts/{account_id}/clone-voice")
async def clone_voice(account_id: int, file: UploadFile = File(...), db: Session = Depends(get_db)):
    """Create an LMNT instant voice clone from a recorded mic sample and set it as
    this channel's narration voice. The browser posts the recording here."""
    import json as _json

    import httpx

    from backend.config import settings

    if not settings.lmnt_api_key:
        raise HTTPException(400, "Clonagem de voz indisponível (LMNT_API_KEY não configurada).")
    svc = AccountProfileService(db)
    acct = svc.get(account_id)
    if not acct:
        raise HTTPException(404, "conta não encontrada")
    audio = await file.read()
    if len(audio) < 20_000:
        raise HTTPException(400, "Gravação muito curta — fale por ~10 segundos e tente de novo.")
    try:
        async with httpx.AsyncClient(timeout=180) as client:
            r = await client.post(
                "https://api.lmnt.com/v1/ai/voice",
                headers={"X-API-Key": settings.lmnt_api_key},
                data={"metadata": _json.dumps(
                    {"name": f"voz-{acct.display_name or account_id}", "type": "instant"})},
                files={"files": (file.filename or "voice.webm", audio, file.content_type or "audio/webm")},
            )
            r.raise_for_status()
            voice = r.json()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"Falha ao clonar a voz no LMNT: {exc}")
    vid = voice.get("id") or (voice.get("voice") or {}).get("id")
    if not vid:
        raise HTTPException(502, "LMNT não retornou o id da voz clonada.")
    acct.preferred_voice = vid  # narrator detects 'v_...' ids as the LMNT clone
    db.commit()
    return {"ok": True, "voice_id": vid, "account_id": account_id,
            "note": "Voz clonada e definida para este canal."}


@router.post("/accounts/{account_id}/pause")
def pause_account(account_id: int, db: Session = Depends(get_db)):
    AccountProfileService(db).pause(account_id)
    return {"ok": True}


@router.post("/accounts/{account_id}/resume")
def resume_account(account_id: int, db: Session = Depends(get_db)):
    svc = AccountProfileService(db)
    acct = svc.get(account_id)
    if not acct:
        raise HTTPException(404, "conta não encontrada")
    if not acct.credentials_encrypted:
        raise HTTPException(
            400,
            "Conta sem credenciais — reconecte antes de reativar (evita publicações "
            "que falhariam silenciosamente).",
        )
    svc.pause(account_id, "active")
    return {"ok": True}


@router.post("/accounts/{account_id}/disconnect")
def disconnect_account(account_id: int, db: Session = Depends(get_db)):
    """Clear credentials without deleting the account — keeps the profile
    (niche, name, etc.) so it can be reconnected later."""
    svc = AccountProfileService(db)
    acct = svc.get(account_id)
    if not acct:
        raise HTTPException(404, "conta não encontrada")
    acct.credentials_encrypted = None
    acct.channel_id = None
    acct.status = "disconnected"
    db.commit()
    db.refresh(acct)
    return acct.to_dict()


@router.post("/accounts/{account_id}/link")
def link_account(account_id: int, payload: LinkRequest, db: Session = Depends(get_db)):
    svc = AccountProfileService(db)
    acct = svc.link_accounts(account_id, payload.links, payload.mirror_to_linked, payload.mirror_format)
    if not acct:
        raise HTTPException(404, "conta não encontrada")
    return acct.to_dict()


# ---------------------------------------------------------------- Channel Optimizer
class OptimizeApply(BaseModel):
    confirmed_fields: list[str] = Field(default_factory=list)
    # The proposal the user already saw (keys: keywords/description/...), so apply
    # reuses it instead of re-running the LLM. Optional → falls back to a fresh draft.
    proposed: dict | None = None


class ChecklistToggle(BaseModel):
    item_id: str
    done: bool = True


def _optimizer_account(account_id: int, db: Session):
    """Shared guard: account exists, is YouTube, and has live credentials."""
    svc = AccountProfileService(db)
    acct = svc.get(account_id)
    if not acct:
        raise HTTPException(404, "conta não encontrada")
    if acct.platform != "youtube":
        raise HTTPException(400, "Otimização de canal disponível apenas para YouTube.")
    if not svc.has_valid_credentials(acct):
        raise HTTPException(400, "Conecte a conta do YouTube antes de otimizar.")
    return svc, acct


@router.get("/accounts/{account_id}/optimize")
def get_optimize(account_id: int, db: Session = Depends(get_db)):
    acct = AccountProfileService(db).get(account_id)
    if not acct:
        raise HTTPException(404, "conta não encontrada")
    return acct.channel_optimization or {}


@router.post("/accounts/{account_id}/optimize/analyze")
async def optimize_analyze(account_id: int, db: Session = Depends(get_db)):
    from backend.agents import channel_optimizer as opt

    svc, acct = _optimizer_account(account_id, db)
    plan = await opt.analyze(acct, svc.get_credentials(acct.id))
    if not plan.get("ok"):
        raise HTTPException(400, plan.get("error", "Falha ao analisar o canal."))
    return plan


@router.post("/accounts/{account_id}/optimize/apply")
async def optimize_apply(account_id: int, payload: OptimizeApply,
                         db: Session = Depends(get_db)):
    from backend.agents import channel_optimizer as opt

    svc, acct = _optimizer_account(account_id, db)
    res = await opt.apply(acct, svc.get_credentials(acct.id), payload.confirmed_fields,
                          client_targets=payload.proposed)
    db.commit()  # persists acct.channel_optimization mutated inside apply()
    if not res.get("ok"):
        raise HTTPException(400, res.get("error", "Falha ao aplicar a otimização."))
    return res


@router.post("/accounts/{account_id}/optimize/checklist")
def optimize_checklist(account_id: int, payload: ChecklistToggle,
                       db: Session = Depends(get_db)):
    acct = AccountProfileService(db).get(account_id)
    if not acct:
        raise HTTPException(404, "conta não encontrada")
    state = dict(acct.channel_optimization or {})
    done = set(state.get("checklist_done") or [])
    done.add(payload.item_id) if payload.done else done.discard(payload.item_id)
    state["checklist_done"] = sorted(done)
    acct.channel_optimization = state
    db.commit()
    return state


# ---------------------------------------------------------------- OAuth
@router.get("/auth/{platform}/start")
def oauth_start(platform: str, account_id: int = Query(...), db: Session = Depends(get_db)):
    mod = PLATFORMS.get(platform)
    if not mod:
        raise HTTPException(400, "plataforma inválida")
    svc = AccountProfileService(db)
    acct = svc.get(account_id)
    if not acct:
        raise HTTPException(404, "conta não encontrada (crie a conta antes de conectar)")
    force_consent = True
    if platform == "youtube":
        force_consent = acct.status in {"auth_error", "disconnected"} or not svc.has_valid_credentials(acct)
    try:
        result = mod.build_auth_url(state=str(account_id), force_consent=force_consent)
    except TypeError:
        result = mod.build_auth_url(state=str(account_id))
    if not result.get("ok"):
        raise HTTPException(400, result.get("error", "OAuth indisponível"))
    return {"auth_url": result["auth_url"]}


@router.get("/auth/{platform}/callback", response_class=HTMLResponse)
def oauth_callback(platform: str, code: str = Query(""), state: str = Query(""),
                   error: str = Query(""), db: Session = Depends(get_db)):
    if error:
        return _html(f"Autorização cancelada: {error}")
    mod = PLATFORMS.get(platform)
    if not mod or not code or not state.isdigit():
        return _html("Callback inválido.")
    account_id = int(state)
    svc = AccountProfileService(db)
    acct = svc.get(account_id)
    if not acct:
        return _html("Conta não encontrada.")

    result = mod.exchange_code(code)
    if not result.get("ok"):
        svc.mark_auth_error(account_id)
        return _html(f"Falha ao conectar {platform}: {result.get('error')}")

    svc.set_credentials(account_id, result["credentials"])
    channel = result.get("channel") or {}
    if channel.get("channel_id"):
        acct.channel_id = channel["channel_id"]
        if channel.get("title"):
            acct.display_name = channel["title"]
    db.commit()

    # The token is freshly valid (the exchange just succeeded) — this is the only
    # moment we KNOW it works. Auto-resume every job that was parked waiting on this
    # reconnect: re-publish if the rendered video survives, else re-render. No button.
    resumed: list[int] = []
    try:
        from backend.pipeline.dispatch import resume_account_blocked_jobs
        resumed = resume_account_blocked_jobs(account_id)
    except Exception:  # noqa: BLE001 — never break the success page over a resume hiccup
        resumed = []
    extra = (f" {len(resumed)} vídeo(s) voltando à produção automaticamente."
             if resumed else "")
    return _html(f"✓ {platform.capitalize()} conectado com sucesso!{extra} "
                 "Pode fechar esta janela.")


def _html(msg: str) -> HTMLResponse:
    return HTMLResponse(
        f"""<!doctype html><html><head><meta charset="utf-8"><title>OAuth</title></head>
        <body style="font-family:system-ui;background:#0A0A0F;color:#F0F0FF;display:flex;
        align-items:center;justify-content:center;height:100vh;margin:0">
        <div style="text-align:center"><h2>{msg}</h2>
        <script>setTimeout(()=>window.close(),2500)</script></div></body></html>"""
    )
