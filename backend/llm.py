"""
Unified LLM client with automatic fallback: Groq -> Gemini -> Ollama.

Uses plain HTTP (httpx) so we don't depend on three SDKs staying in sync.
Every call returns text; `complete_json` additionally parses a JSON object out
of the response (tolerating markdown fences and leading prose).

If no provider is configured/reachable, raises LLMUnavailable so callers can
fall back to deterministic offline templates.
"""
from __future__ import annotations

import json
import logging
import re

import httpx

from backend.config import settings

logger = logging.getLogger("studio.llm")

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODEL = "llama-3.3-70b-versatile"
GEMINI_MODEL = "gemini-1.5-flash"
GEMINI_URL = (
    "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
)


class LLMUnavailable(Exception):
    """No configured LLM provider succeeded."""


async def _try_groq(system: str | None, prompt: str, json_mode: bool, max_tokens: int) -> str | None:
    if not settings.groq_api_key:
        return None
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    payload: dict = {"model": GROQ_MODEL, "messages": messages, "max_tokens": max_tokens, "temperature": 0.8}
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            r = await client.post(
                GROQ_URL,
                headers={"Authorization": f"Bearer {settings.groq_api_key}"},
                json=payload,
            )
            r.raise_for_status()
            return r.json()["choices"][0]["message"]["content"]
    except Exception as exc:  # noqa: BLE001
        logger.warning("Groq failed: %s", exc)
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


def available() -> bool:
    return bool(settings.groq_api_key or settings.gemini_api_key)
