"""Translates raw exception text (Google/YouTube API errors, network errors,
LLM-quota errors, etc.) into a short explanation a non-technical user can act
on. The raw `error_message` is still stored/shown for debugging — this is only
an added `error_message_friendly` field, never a replacement."""
from __future__ import annotations

_RULES: tuple[tuple[tuple[str, ...], str], ...] = (
    (
        ("invalid_grant", "expired or revoked", "credenciais conectadas"),
        "A conexão com a conta Google/YouTube expirou. Reconecte o canal em "
        "Plataformas — os vídeos presos vão voltar sozinhos assim que reconectar.",
    ),
    (
        ("exceeded the number of videos", "uploadLimitExceeded"),
        "O YouTube bloqueou o envio por limite diário de uploads dessa conta "
        "(comum em canais não verificados). O sistema tenta de novo sozinho mais tarde.",
    ),
    (
        ("quotaExceeded", "dailyLimitExceeded"),
        "A cota diária da API do YouTube acabou por hoje. Volta a funcionar "
        "sozinho quando a cota renovar.",
    ),
    (
        ("PODE já estar no canal", "Verifique o YouTube"),
        "O envio foi interrompido no meio e o vídeo PODE já estar publicado no "
        "canal. Verifique manualmente antes de reenviar, para não duplicar.",
    ),
    (
        ("Render interrompido",),
        "A geração do vídeo travou no meio (provável falta de memória no "
        "servidor). Precisa de um novo envio manual.",
    ),
    (
        ("Falha ao baixar video do Drive",),
        "Falha ao baixar o vídeo do Google Drive (rede ou permissão). "
        "O sistema tenta de novo automaticamente.",
    ),
    (
        ("scriptwriter esgotou", "LLM indisponível", "esgotou (LLM"),
        "A cota gratuita de IA para roteiro esgotou por hoje. Tenta de novo "
        "automaticamente mais tarde.",
    ),
    (
        ("insufficient", "forbidden", "HttpError 403"),
        "A plataforma negou a permissão. Verifique se a conta conectada tem "
        "acesso a esse recurso.",
    ),
)


def friendly_error(msg: str | None) -> str | None:
    """Best-effort plain-Portuguese explanation of a raw error message.

    Falls back to the original message when no known pattern matches — we
    never hide a genuinely novel error behind a made-up explanation.
    """
    if not msg:
        return None
    for markers, explanation in _RULES:
        if any(marker in msg for marker in markers):
            return explanation
    return msg
