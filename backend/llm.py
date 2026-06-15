"""
Unified LLM client with automatic fallback: Groq -> Gemini -> Ollama.

Uses plain HTTP (httpx) so we don't depend on three SDKs staying in sync.
Every call returns text; `complete_json` additionally parses a JSON object out
of the response (tolerating markdown fences and leading prose).

If no provider is configured/reachable, raises LLMUnavailable so callers can
fall back to deterministic offline templates.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time

import httpx

from backend.config import settings

logger = logging.getLogger("studio.llm")

# Circuit breaker: once Groq starts rate-limiting (429), every call would waste
# ~30s of backoff before falling through to Gemini. After a tripped call we skip
# Groq entirely for a cooldown and go straight to Gemini — so a whole video's ~8
# LLM calls don't each eat the full backoff. Re-probes Groq after the cooldown.
_GROQ_COOLDOWN_S = 120.0
_groq_blocked_until = 0.0

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODEL = "llama-3.3-70b-versatile"
# gemini-1.5-flash was retired (404 on this key) — the Gemini fallback silently
# never ran. 2.5-flash is current and supports Google Search grounding.
GEMINI_MODEL = "gemini-2.5-flash"
# Tried in order for grounded research: the free tier rate-limits (429) per model,
# so falling through to sibling models keeps grounding working under load.
GEMINI_GROUNDED_MODELS = ["gemini-2.5-flash", "gemini-flash-latest", "gemini-2.0-flash", "gemini-2.5-flash-lite"]
GEMINI_URL = (
    "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
)


class LLMUnavailable(Exception):
    """No configured LLM provider succeeded."""


async def _try_groq(system: str | None, prompt: str, json_mode: bool, max_tokens: int) -> str | None:
    global _groq_blocked_until
    if not settings.groq_api_key:
        return None
    # Circuit open: skip Groq, let complete() fall straight to Gemini.
    if time.monotonic() < _groq_blocked_until:
        return None
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    payload: dict = {"model": GROQ_MODEL, "messages": messages, "max_tokens": max_tokens, "temperature": 0.8}
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
    # Each video makes ~8 LLM calls in seconds (script + growth agents + SEO); the
    # free tier rate-limits (429) under that burst. Try a couple of short backoffs;
    # if still limited, trip the breaker and let Gemini take over (much faster than
    # eating the full backoff on every subsequent call).
    for attempt in range(2):
        try:
            async with httpx.AsyncClient(timeout=60) as client:
                r = await client.post(
                    GROQ_URL,
                    headers={"Authorization": f"Bearer {settings.groq_api_key}"},
                    json=payload,
                )
                if r.status_code == 429:
                    try:
                        wait = float(r.headers.get("retry-after", "") or 0)
                    except ValueError:
                        wait = 0
                    wait = min(wait or (1.5 * (attempt + 1)), 4.0)
                    logger.info("Groq 429 — backoff %.1fs (tentativa %d/2)", wait, attempt + 1)
                    await asyncio.sleep(wait)
                    continue
                r.raise_for_status()
                return r.json()["choices"][0]["message"]["content"]
        except Exception as exc:  # noqa: BLE001
            logger.warning("Groq failed: %s", exc)
            return None
    # Still rate-limited after retries — open the circuit so the next ~cooldown of
    # calls skip Groq and use Gemini directly.
    _groq_blocked_until = time.monotonic() + _GROQ_COOLDOWN_S
    logger.warning("Groq rate-limited; pulando Groq por %.0fs (usando Gemini).", _GROQ_COOLDOWN_S)
    return None


async def _try_gemini(system: str | None, prompt: str, json_mode: bool, max_tokens: int) -> str | None:
    if not settings.gemini_api_key:
        return None
    full = f"{system}\n\n{prompt}" if system else prompt
    gen_cfg: dict = {"maxOutputTokens": max_tokens, "temperature": 0.8}
    if json_mode:
        gen_cfg["responseMimeType"] = "application/json"
    payload = {"contents": [{"parts": [{"text": full}]}], "generationConfig": gen_cfg}
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            r = await client.post(
                GEMINI_URL.format(model=GEMINI_MODEL),
                params={"key": settings.gemini_api_key},
                json=payload,
            )
            r.raise_for_status()
            data = r.json()
            return data["candidates"][0]["content"]["parts"][0]["text"]
    except Exception as exc:  # noqa: BLE001
        logger.warning("Gemini failed: %s", exc)
        return None


async def _try_ollama(system: str | None, prompt: str, json_mode: bool, max_tokens: int) -> str | None:
    full = f"{system}\n\n{prompt}" if system else prompt
    payload = {"model": "llama3.1", "prompt": full, "stream": False}
    if json_mode:
        payload["format"] = "json"
    try:
        async with httpx.AsyncClient(timeout=120) as client:
            r = await client.post(f"{settings.ollama_host}/api/generate", json=payload)
            r.raise_for_status()
            return r.json().get("response")
    except Exception as exc:  # noqa: BLE001
        logger.debug("Ollama unavailable: %s", exc)
        return None


async def complete(
    prompt: str,
    system: str | None = None,
    json_mode: bool = False,
    max_tokens: int = 2048,
) -> str:
    """Return completion text from the first available provider."""
    for provider in (_try_groq, _try_gemini, _try_ollama):
        text = await provider(system, prompt, json_mode, max_tokens)
        if text:
            return text
    raise LLMUnavailable(
        "Nenhum provedor LLM disponível (configure GROQ_API_KEY ou GEMINI_API_KEY, "
        "ou rode Ollama localmente)."
    )


def extract_json(text: str) -> dict:
    """Pull the first JSON object out of an LLM response."""
    text = text.strip()
    # Strip ```json ... ``` fences.
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if fence:
        text = fence.group(1).strip()
    # Find the outermost {...}.
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        text = text[start : end + 1]
    return json.loads(text)


async def complete_json(
    prompt: str,
    system: str | None = None,
    max_tokens: int = 2048,
) -> dict:
    raw = await complete(prompt, system=system, json_mode=True, max_tokens=max_tokens)
    return extract_json(raw)


async def research(query: str, max_tokens: int = 1200) -> dict:
    """Grounded factual lookup via Gemini + Google Search.

    Returns {"facts": str, "sources": [{"title","uri"}], "grounded": bool}. NEVER
    raises — on any failure returns grounded=False with empty facts so the caller
    can proceed in 'cautious' mode (state nothing it can't verify). This is what
    stops the scriptwriter from inventing scores/dates/names for real events.
    """
    empty = {"facts": "", "sources": [], "grounded": False}
    if not settings.gemini_api_key or not query.strip():
        return empty
    prompt = (
        "Você é um pesquisador factual. Pesquise na web e responda com FATOS REAIS, "
        "verificados e ATUAIS sobre o tema abaixo. Liste em tópicos curtos: o que "
        "aconteceu, datas exatas, placares/números exatos, nomes corretos. Se algo "
        "não puder ser confirmado, escreva 'não confirmado'. NÃO invente nada.\n\n"
        f"TEMA: {query.strip()}"
    )
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "tools": [{"google_search": {}}],
        "generationConfig": {"maxOutputTokens": max_tokens, "temperature": 0.2},
    }
    last_exc = None
    for model in GEMINI_GROUNDED_MODELS:
        try:
            async with httpx.AsyncClient(timeout=90) as client:
                r = await client.post(
                    GEMINI_URL.format(model=model),
                    params={"key": settings.gemini_api_key},
                    json=payload,
                )
                if r.status_code == 429:  # this model is rate-limited — try the next
                    last_exc = "429"
                    continue
                r.raise_for_status()
                cand = r.json()["candidates"][0]
                facts = "".join(p.get("text", "") for p in cand["content"]["parts"]).strip()
                gm = cand.get("groundingMetadata", {}) or {}
                sources = []
                for c in (gm.get("groundingChunks") or []):
                    w = c.get("web") or {}
                    if w.get("uri"):
                        sources.append({"title": w.get("title", ""), "uri": w["uri"]})
                if facts:
                    return {"facts": facts, "sources": sources, "grounded": True}
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            continue
    logger.warning("Gemini research (grounding) unavailable (last: %s)", last_exc)
    return empty


def available() -> bool:
    return bool(settings.groq_api_key or settings.gemini_api_key)
