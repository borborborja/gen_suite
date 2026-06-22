"""Redis pub/sub bus + SSE relay for job progress.

Producers (ARQ workers, from Phase 4) call ``publish(tenant, job, event)``; the SSE endpoint
subscribes to the same per-job channel and streams events to the browser. A periodic comment
line keeps the connection alive through proxies.
"""
from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator

import redis.asyncio as redis

from ..settings import settings

_redis: redis.Redis | None = None


def get_redis() -> redis.Redis:
    global _redis
    if _redis is None:
        _redis = redis.from_url(settings.redis_url, decode_responses=True)
    return _redis


def _channel(tenant_id: uuid.UUID, job_id: uuid.UUID) -> str:
    return f"events:{tenant_id}:{job_id}"


async def publish(tenant_id: uuid.UUID, job_id: uuid.UUID, event: dict) -> None:
    """Best-effort: progress events are informative only (the DB is the source of truth), so a
    transient Redis hiccup must never fail the task that's publishing them."""
    try:
        await get_redis().publish(_channel(tenant_id, job_id), json.dumps(event))
    except Exception:
        pass


# Event kinds that mean the job is over — the SSE relay closes the stream when it sees one.
_TERMINAL_KINDS = {"all_done", "book_fail", "cancelled", "error"}


async def sse_stream(
    tenant_id: uuid.UUID, job_id: uuid.UUID, *, already_done: dict | None = None,
    check_terminal=None, max_seconds: int = 1800,
) -> AsyncIterator[str]:
    """Relay a job's progress events to the browser. If ``already_done`` is given, the job had
    already reached a terminal state before the client connected (pub/sub has no history), so we
    emit one synthetic final event and close instead of keeping the connection open forever.

    Resilience: while streaming, after ~30s of silence we re-check the job's DB status via
    ``check_terminal`` (async callable → final-event dict or None) — so a job that died mid-stream
    (worker crash, reaper, orphan) closes the stream and the UI clears instead of hanging. A hard
    ``max_seconds`` cap guarantees the connection never leaks."""
    pubsub = get_redis().pubsub()
    await pubsub.subscribe(_channel(tenant_id, job_id))
    try:
        yield ": connected\n\n"
        if already_done is not None:
            yield f"data: {json.dumps(already_done)}\n\n"
            return
        quiet = 0
        elapsed = 0
        while True:
            msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=15.0)
            if msg and msg.get("type") == "message":
                quiet = 0
                yield f"data: {msg['data']}\n\n"
                try:
                    if json.loads(msg["data"]).get("kind") in _TERMINAL_KINDS:
                        return  # job finished — close the stream so the client stops waiting
                except (ValueError, TypeError):
                    pass
            else:
                yield ": keepalive\n\n"
                quiet += 1
                elapsed += 15
                if check_terminal is not None and quiet >= 2:  # ~30s silent → did the job die quietly?
                    quiet = 0
                    try:
                        done = await check_terminal()
                    except Exception:
                        done = None
                    if done is not None:
                        yield f"data: {json.dumps(done)}\n\n"
                        return
                if elapsed >= max_seconds:
                    yield 'data: {"kind": "book_fail", "error": "stream timeout"}\n\n'
                    return
    finally:
        await pubsub.unsubscribe(_channel(tenant_id, job_id))
        await pubsub.aclose()
