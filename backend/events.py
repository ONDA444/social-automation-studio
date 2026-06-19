"""
Live event bus for the dashboard (AgentLog WebSocket).

Two transport paths, chosen automatically:
  * In-process: when the pipeline runs inside the API process (local dev without
    Celery), events are broadcast straight to connected WebSocket clients.
  * Cross-process: when a Celery worker produces events, it publishes to a Redis
    channel; the API subscribes and re-broadcasts to clients.

`publish_event()` is safe to call from anywhere — sync code, threads, or Celery
workers. It never raises; the dashboard is best-effort.
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime
from typing import Any

from backend.config import settings

logger = logging.getLogger("studio.events")

EVENT_CHANNEL = "studio:events"

# Reference to the API's event loop, captured at startup, so worker threads can
# schedule coroutines onto it.
_main_loop: asyncio.AbstractEventLoop | None = None
_redis = None  # lazy sync redis client for cross-process publish


def set_main_loop(loop: asyncio.AbstractEventLoop) -> None:
    global _main_loop
    _main_loop = loop


class ConnectionManager:
    def __init__(self) -> None:
        self.active: list[Any] = []

    async def connect(self, ws) -> None:
        await ws.accept()
        self.active.append(ws)

    def disconnect(self, ws) -> None:
        if ws in self.active:
            self.active.remove(ws)

    async def broadcast(self, message: dict) -> None:
        dead = []
        for ws in list(self.active):
            try:
                await ws.send_json(message)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws)


manager = ConnectionManager()


def _get_redis():
    global _redis
    if _redis is None:
        try:
            import redis  # type: ignore

            from backend.config import settings

            _redis = redis.from_url(settings.redis_url, decode_responses=True)
            _redis.ping()
        except Exception:
            _redis = False  # mark as unavailable
    return _redis or None


def publish_event(event: dict) -> None:
    """Fire-and-forget. Adds a timestamp and delivers to connected WS clients.

    Single-process deploy (the Railway default: API + scheduler + in-process
    pipeline all in ONE process, no Celery worker) → broadcast STRAIGHT to the
    WebSocket clients. We deliberately skip the Redis pub/sub round-trip here:
    it only exists to bridge a SEPARATE Celery worker process back to the API,
    and when Redis is up `publish_event` used to `return` right after publishing
    — so a single hiccup in the relay silently swallowed EVERY live event (the
    AgentLog stuck on "Sem eventos ainda" while jobs were clearly running).
    In-process delivery has no relay to break and no network hop.
    """
    event.setdefault("ts", datetime.utcnow().isoformat())

    if not settings.use_celery:
        _broadcast_threadsafe(event)
        return

    # Cross-process path (Celery worker -> API): publish to Redis; redis_listener
    # on the API side relays to WS clients.
    r = _get_redis()
    if r is not None:
        try:
            r.publish(EVENT_CHANNEL, json.dumps(event))
            return
        except Exception:
            pass  # fall through to in-process

    # In-process fallback.
    _broadcast_threadsafe(event)


def _broadcast_threadsafe(event: dict) -> None:
    if _main_loop is None:
        return
    try:
        asyncio.run_coroutine_threadsafe(manager.broadcast(event), _main_loop)
    except Exception as exc:  # pragma: no cover
        logger.debug("event broadcast failed: %s", exc)


async def redis_listener() -> None:
    """Background task: relay Redis pub/sub events to WebSocket clients."""
    try:
        import redis.asyncio as aioredis  # type: ignore

        from backend.config import settings

        client = aioredis.from_url(settings.redis_url, decode_responses=True)
        await client.ping()
    except Exception:
        logger.info("Redis not available — AgentLog runs in in-process mode only.")
        return

    pubsub = client.pubsub()
    await pubsub.subscribe(EVENT_CHANNEL)
    logger.info("Subscribed to Redis channel '%s' for live events.", EVENT_CHANNEL)
    try:
        async for msg in pubsub.listen():
            if msg.get("type") != "message":
                continue
            try:
                await manager.broadcast(json.loads(msg["data"]))
            except Exception:
                continue
    except asyncio.CancelledError:
        await pubsub.unsubscribe(EVENT_CHANNEL)
        raise
