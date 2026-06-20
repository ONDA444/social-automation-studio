"""
Unified LLM client with automatic fallback: Groq -> Gemini -> OpenRouter -> Ollama.

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


class _RateLimiter:
    """Paces calls so a video's burst (~8 LLM calls in seconds) never trips a
    provider's per-minute rate limit. Enforces a minimum gap between requests
    based on the configured RPM. The in-process worker is serial (one video at a
    time) so a single shared limiter per provider fully covers concurrency."""

    def __init__(self, rpm: int) -> None:
        self.min_interval = 60.0 / max(1, rpm)
        self._last = 0.0
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            now = time.monotonic()
            wait = self.min_interval - (now - self._last)
            if wait > 0:
                await asyncio.sleep(wait)
            self._last = time.monotonic()


# Lazily built from settings so RPM is tunable via env (GROQ_RPM / GEMINI_RPM).
_groq_limiter: _RateLimiter | None = None
_gemini_limiter: _RateLimiter | None = None


def _limiter(provider: str) -> _RateLimiter:
    global _groq_limiter, _gemini_limiter
    if provider == "groq":
        if _groq_limiter is None:
            _groq_limiter = _RateLimiter(settings.groq_rpm)
        return _groq_limiter
    if _gemini_limiter is None:
        _gemini_limiter = _RateLimiter(settings.gemini_rpm)
    return _gemini_limiter

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODEL = "llama-3.3-70b-versatile"
# When the 70b model is rate-limited (429) but the account still has quota, the
# fast 8b model often answers — try it before tripping the breaker / falling to
# the next provider, so a momentary 70b limit doesn't cost the whole script.
GROQ_FALLBACK_MODEL = "llama-3.1-8b-instant"
# gemini-1.5-flash was retired (404 on this key) — the Gemini fallback silently
# never ran. 2.5-flash is current and supports Google Search grounding.
GEMINI_MODEL = "gemini-2.5-flash"
# Tried in order for grounded research: the free tier rate-limits (429) per model,
# so falling through to sibling models keeps grounding working under load.
GEMINI_GROUNDED_MODELS = ["gemini-2.5-flash", "gemini-flash-latest", "gemini-2.0-flash", "gemini-2.5-flash-lite"]
# Fallback order for completion (no grounding): try cheaper/faster models when the
# primary is rate-limited (429) so a burst of LLM calls doesn't all fail together.
GEMINI_COMPLETION_MODELS = ["gemini-2.5-flash", "gemini-2.5-flash-lite", "gemini-2.0-flash"]
GEMINI_URL = (
    "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
)


class LLMUnavailable(Exception):
    """No configured LLM provider succeeded."""


async def _try_groq(system: str | None, prompt: str, json_mode: bool, max_tokens: int, fast: bool = False) -> str | None:
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
    # fast=True → use the lighter 8b model as PRIMARY to spare the scarce 70b daily
    # quota for the script itself; cheap creative rewrites (hook/retention/shorts)
    # don't need 70b. This directly relieves the exhaustion behind "LLM indisponível".
    primary = GROQ_FALLBACK_MODEL if fast else GROQ_MODEL
    payload: dict = {"model": primary, "messages": messages, "max_tokens": max_tokens, "temperature": 0.8}
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
    # Each video makes ~8 LLM calls in seconds (script + growth agents + SEO); the
    # free tier rate-limits (429) under that burst. Try a couple of short backoffs;
    # if still limited, trip the breaker and let Gemini take over (much faster than
    # eating the full backoff on every subsequent call).
    for attempt in range(2):
        try:
            await _limiter("groq").acquire()
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
    # 70b still rate-limited — try the fast 8b model once (separate capacity) before
    # giving up on Groq. Skipped when we were ALREADY on 8b (fast=True): no heavier
    # Groq model left to try, so fall straight through to the next provider.
    if not fast:
        try:
            await _limiter("groq").acquire()
            async with httpx.AsyncClient(timeout=60) as client:
                r = await client.post(
                    GROQ_URL,
                    headers={"Authorization": f"Bearer {settings.groq_api_key}"},
                    json={**payload, "model": GROQ_FALLBACK_MODEL},
                )
                if r.status_code != 429:
                    r.raise_for_status()
                    return r.json()["choices"][0]["message"]["content"]
        except Exception as exc:  # noqa: BLE001
            logger.warning("Groq %s failed: %s", GROQ_FALLBACK_MODEL, exc)
    # Both Groq models rate-limited — open the circuit so the next ~cooldown of
    # calls skip Groq and use the next provider directly.
    _groq_blocked_until = time.monotonic() + _GROQ_COOLDOWN_S
    logger.warning("Groq rate-limited; pulando Groq por %.0fs (usando próximo provedor).", _GROQ_COOLDOWN_S)
    return None


async def _try_gemini(system: str | None, prompt: str, json_mode: bool, max_tokens: int, fast: bool = False) -> str | None:
    if not settings.gemini_api_key:
        return None
    full = f"{system}\n\n{prompt}" if system else prompt
    gen_cfg: dict = {"maxOutputTokens": max_tokens, "temperature": 0.8}
    if json_mode:
        gen_cfg["responseMimeType"] = "application/json"
    payload = {"contents": [{"parts": [{"text": full}]}], "generationConfig": gen_cfg}
    # fast=True → prefer the lite/flash models first (cheaper, higher free limits).
    models = ["gemini-2.5-flash-lite", "gemini-2.0-flash"] if fast else GEMINI_COMPLETION_MODELS
    for model in models:
        try:
            await _limiter("gemini").acquire()
            async with httpx.AsyncClient(timeout=60) as client:
                r = await client.post(
                    GEMINI_URL.format(model=model),
                    params={"key": settings.gemini_api_key},
                    json=payload,
                )
                if r.status_code == 429:
                    logger.info("Gemini %s 429 — tentando próximo modelo.", model)
                    continue
                r.raise_for_status()
                data = r.json()
                return data["candidates"][0]["content"]["parts"][0]["text"]
        except Exception as exc:  # noqa: BLE001
            logger.warning("Gemini %s failed: %s", model, exc)
            continue
    return None


OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
# Free models on OpenRouter — their quota is INDEPENDENT of Groq/Gemini, so when
# both of those free tiers are exhausted (the recurring "LLM indisponível"), these
# keep scripts flowing. Tried in order; each ':free' model has its own availability,
# so a 429/5xx on one falls through to the next instead of failing the call.
# Slugs ROT regularly: OpenRouter retires ':free' models without notice (a retired
# slug 404s). Keep this list to models confirmed live and spread across DISTINCT
# providers (Meta/OpenAI/Qwen/NVIDIA/Google) so a single provider's upstream 429
# falls through to a different provider instead of stalling the whole fallback.
# Ordered strong→light; fast=True reverses it to try the lightest first.
OPENROUTER_MODELS = [
    "meta-llama/llama-3.3-70b-instruct:free",
    "openai/gpt-oss-120b:free",
    "qwen/qwen3-next-80b-a3b-instruct:free",
    "nvidia/nemotron-3-super-120b-a12b:free",
    "google/gemma-4-31b-it:free",
    "meta-llama/llama-3.2-3b-instruct:free",
]


async def _try_openrouter(system: str | None, prompt: str, json_mode: bool, max_tokens: int, fast: bool = False) -> str | None:
    if not settings.openrouter_api_key:
        return None
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    headers = {"Authorization": f"Bearer {settings.openrouter_api_key}"}
    base: dict = {"messages": messages, "max_tokens": max_tokens, "temperature": 0.8}
    if json_mode:
        base["response_format"] = {"type": "json_object"}
    # fast=True → try the lighter 8b free model first (last in the list).
    models = list(reversed(OPENROUTER_MODELS)) if fast else OPENROUTER_MODELS
    for model in models:
        try:
            async with httpx.AsyncClient(timeout=90) as client:
                r = await client.post(OPENROUTER_URL, headers=headers, json={**base, "model": model})
                # 400/401/403/404 happen when a :free model isn't available to the
                # account (commonly: the data policy isn't enabled at
                # openrouter.ai/settings/privacy) — skip to the next model instead of
                # blowing up, so one bad model never sinks the whole provider.
                if r.status_code in (400, 401, 403, 404, 429, 502, 503):
                    logger.info("OpenRouter %s %s — tentando próximo modelo.", model, r.status_code)
                    continue
                r.raise_for_status()
                data = r.json()
                content = (data.get("choices") or [{}])[0].get("message", {}).get("content")
                if content:
                    return content
        except Exception as exc:  # noqa: BLE001
            logger.warning("OpenRouter %s failed: %s", model, exc)
            continue
    return None


async def _try_ollama(system: str | None, prompt: str, json_mode: bool, max_tokens: int, fast: bool = False) -> str | None:
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
    fast: bool = False,
) -> str:
    """Return completion text from the first available provider.

    fast=True routes the call to the lighter/cheaper model of each provider (Groq
    8b, Gemini flash-lite, OpenRouter 8b) — use it for short, formulaic calls
    (hooks, captions, hashtags) so they don't burn the scarce strong-model quota
    that the script itself needs.
    """
    for provider in (_try_groq, _try_gemini, _try_openrouter, _try_ollama):
        text = await provider(system, prompt, json_mode, max_tokens, fast)
        if text:
            return text
    raise LLMUnavailable(
        "Nenhum provedor LLM disponível (configure GROQ_API_KEY, GEMINI_API_KEY ou "
        "OPENROUTER_API_KEY, ou rode Ollama localmente)."
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
    fast: bool = False,
) -> dict:
    raw = await complete(prompt, system=system, json_mode=True, max_tokens=max_tokens, fast=fast)
    try:
        return extract_json(raw)
    except (json.JSONDecodeError, ValueError) as exc:
        # Weak free models (especially the 3B 'fast' tier) sometimes emit truncated or
        # malformed JSON. A raw JSONDecodeError would bubble up as an uncaught crash;
        # instead treat it like the provider being unavailable so every caller degrades
        # the SAME graceful way it already handles LLMUnavailable (the scriptwriter
        # retries for real content; growth agents fall back to their offline path).
        snippet = (raw or "").replace("\n", " ")[:180]
        logger.warning("complete_json: JSON inválido do modelo (%d chars) — tratando como indisponível: %s",
                       len(raw or ""), snippet)
        raise LLMUnavailable("modelo retornou JSON inválido") from exc


async def research(query: str, max_tokens: int = 1200) -> dict:
    """Grounded factual lookup via Gemini + Google Search.

    Returns {"facts": str, "sources": [{"title","uri"}], "grounded": bool,
    "unavailable": bool}. NEVER raises. `unavailable` distinguishes two very different
    "grounded=False" cases so the caller can react correctly:
      * unavailable=False — grounding is not configured / not needed (no Gemini key,
        empty query). Permanent; proceed in 'cautious' mode (retrying won't help).
      * unavailable=True  — Gemini was TRIED but every model 429'd / errored. Transient;
        a factual/trending topic should RETRY rather than ship an ungrounded (generic)
        script. This is the recurring 429 storm that produced off-topic videos.
    """
    empty = {"facts": "", "sources": [], "grounded": False, "unavailable": False}
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
            await _limiter("gemini").acquire()
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
                    return {"facts": facts, "sources": sources, "grounded": True,
                            "unavailable": False}
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            continue
    logger.warning("Gemini research (grounding) unavailable (last: %s)", last_exc)
    # Every model was tried and none answered (429 / network) → transient outage.
    return {**empty, "unavailable": True}


def available() -> bool:
    return bool(settings.groq_api_key or settings.gemini_api_key or settings.openrouter_api_key)
