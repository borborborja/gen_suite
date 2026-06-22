"""AI provider registry: per-task credential resolution + credential/binding management.

``ProviderService.resolve`` follows a ``cfg or binding or catalog-default`` chain so
transcription, embeddings and inference all pick a provider/model/key the same way.
The decrypted key is returned only to in-process callers (workers), never via the API.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.crypto import get_secret_box
from ...models.provider import ProviderCredential, TaskProviderBinding
from .catalog import PROVIDER_CATALOG, TASK_CAPABILITY


@dataclass
class ResolvedCredential:
    engine: str
    model: str | None
    api_key: str | None
    base_url: str | None
    params: dict

    def to_engine_kwargs(self) -> dict:
        return {
            "engine": self.engine,
            "model": self.model,
            "api_key": self.api_key,
            "base_url": self.base_url,
        }


EMBEDDING_DIM = 1024


def embed_texts(rc: ResolvedCredential, texts: list[str]) -> list[list[float]]:
    """Embed texts via an OpenAI-compatible endpoint (OpenAI/OpenRouter/Ollama). Sync — call
    from a thread. Standardized on 1024 dims to match the transcriptions.embedding column."""
    from openai import OpenAI

    if rc.engine not in ("openai", "ollama", "jina"):
        raise ValueError(f"engine '{rc.engine}' does not support embeddings")
    # Bound each call so a throttled/hung provider fails fast instead of blocking the worker job
    # (which now has a multi-hour job_timeout). A failed page is handled per-page upstream.
    client = OpenAI(api_key=rc.api_key or "none", base_url=rc.base_url, timeout=60.0, max_retries=2)
    kwargs: dict = {"model": rc.model, "input": texts}
    if rc.engine in ("openai", "jina"):  # both accept an explicit output dimensionality
        kwargs["dimensions"] = EMBEDDING_DIM
    resp = client.embeddings.create(**kwargs)
    vectors = [d.embedding for d in resp.data]
    for v in vectors:
        if len(v) != EMBEDDING_DIM:
            raise ValueError(f"embedding dim {len(v)} != {EMBEDDING_DIM}; choose a 1024-dim model")
    return vectors


def extract_structured_with_usage(
    rc: ResolvedCredential, text: str, *, schema: dict, system: str, schema_name: str = "extraction"
) -> tuple[dict, dict]:
    """Like ``extract_structured`` but also returns token usage ``{prompt, completion, total}`` for
    the M3 cost probe (plan §M3). Sync — call from a thread."""
    import json

    from openai import OpenAI

    if rc.engine == "tesseract":
        raise ValueError("engine 'tesseract' cannot do inference")
    # Per-call timeout so a throttled/hung provider fails fast (the page is retried later via the
    # extraction anti-join) instead of stalling the whole job under the multi-hour job_timeout.
    client = OpenAI(api_key=rc.api_key or "none", base_url=rc.base_url, timeout=90.0, max_retries=2)
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": text},
    ]
    response_format = {
        "type": "json_schema",
        "json_schema": {"name": schema_name, "schema": schema, "strict": False},
    }
    try:
        resp = client.chat.completions.create(
            model=rc.model, messages=messages, response_format=response_format, temperature=0,
        )
    except Exception:
        # Model/endpoint lacks json_schema support → retry with plain json_object.
        resp = client.chat.completions.create(
            model=rc.model, messages=messages,
            response_format={"type": "json_object"}, temperature=0,
        )
    content = resp.choices[0].message.content or "{}"
    u = getattr(resp, "usage", None)
    usage = {
        "prompt": getattr(u, "prompt_tokens", 0) or 0,
        "completion": getattr(u, "completion_tokens", 0) or 0,
        "total": getattr(u, "total_tokens", 0) or 0,
    }
    return json.loads(content), usage


def extract_structured(
    rc: ResolvedCredential, text: str, *, schema: dict, system: str, schema_name: str = "extraction"
) -> dict:
    """Run a text→JSON extraction via an OpenAI-compatible chat endpoint (OpenAI/OpenRouter/
    Ollama/Claude-compatible). Sync — call from a thread. Sibling of ``embed_texts`` (plan §2)."""
    data, _ = extract_structured_with_usage(
        rc, text, schema=schema, system=system, schema_name=schema_name
    )
    return data


# ── Batch API (async, ~50% cheaper) over the OpenAI-compatible /batches protocol ──────────────
# Works with providers that expose OpenAI's Files+Batches endpoints (OpenAI; Google Gemini's batch).
# OpenRouter has no batch endpoint, so the task falls back to sync for it. Sync helpers — call from
# a thread.

def _client(rc: ResolvedCredential, timeout: float = 120.0):
    from openai import OpenAI
    return OpenAI(api_key=rc.api_key or "none", base_url=rc.base_url, timeout=timeout, max_retries=2)


def submit_chat_batch(
    rc: ResolvedCredential, items: list[tuple[str, str]], *, system: str
) -> str:
    """Submit one chat-completion request per (custom_id, user_text). Returns the provider batch id."""
    import json as _json

    lines = []
    for cid, user_text in items:
        lines.append(_json.dumps({
            "custom_id": cid,
            "method": "POST",
            "url": "/v1/chat/completions",
            "body": {
                "model": rc.model,
                "messages": [{"role": "system", "content": system},
                             {"role": "user", "content": user_text}],
                "response_format": {"type": "json_object"},
                "temperature": 0,
            },
        }))
    client = _client(rc)
    f = client.files.create(file=("batch.jsonl", "\n".join(lines).encode()), purpose="batch")
    batch = client.batches.create(
        input_file_id=f.id, endpoint="/v1/chat/completions", completion_window="24h"
    )
    return batch.id


def poll_batch(rc: ResolvedCredential, batch_id: str) -> tuple[str, str | None]:
    """Returns (status, output_file_id). status ∈ validating|in_progress|finalizing|completed|
    failed|expired|cancelled."""
    b = _client(rc).batches.retrieve(batch_id)
    return b.status, getattr(b, "output_file_id", None)


def fetch_batch_results(rc: ResolvedCredential, output_file_id: str) -> dict[str, dict | None]:
    """Map each custom_id → its parsed JSON body (None if that line errored)."""
    import json as _json

    text = _client(rc).files.content(output_file_id).text
    out: dict[str, dict | None] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = _json.loads(line)
            body = (obj.get("response") or {}).get("body") or {}
            content = body["choices"][0]["message"]["content"]
            out[obj["custom_id"]] = _json.loads(content)
        except Exception:
            cid = obj.get("custom_id") if isinstance(obj, dict) else None
            if cid:
                out[cid] = None
    return out


async def record_usage(
    session, *, tenant_id, job_id, task_type: str, model: str | None,
    prompt_tokens: int, completion_tokens: int,
) -> None:
    """Log one AI-spend event (token usage + computed cost) for the spending control. Best-effort —
    never let usage logging break the job."""
    try:
        from ...models.usage import UsageEvent
        from .pricing import cost_cents
        session.add(UsageEvent(
            tenant_id=tenant_id, job_id=job_id, task_type=task_type, model=model,
            prompt_tokens=int(prompt_tokens or 0), completion_tokens=int(completion_tokens or 0),
            cost_cents=cost_cents(model, int(prompt_tokens or 0), int(completion_tokens or 0)),
        ))
        await session.flush()
    except Exception:
        pass


async def assert_within_budget(session, tenant_id) -> None:
    """Raise 402 if the tenant set a monthly AI budget and this month's spend already reached it."""
    from fastapi import HTTPException, status
    from ...models.tenant import Tenant
    budget = await session.scalar(select(Tenant.monthly_budget_cents).where(Tenant.id == tenant_id))
    if budget is None:
        return
    spent = await month_spend_cents(session, tenant_id)
    if spent >= budget:
        raise HTTPException(
            status.HTTP_402_PAYMENT_REQUIRED,
            f"Presupuesto mensual de IA agotado (${spent/100:.2f} de ${budget/100:.2f}). "
            "Súbelo en Ajustes o espera al próximo mes.",
        )


async def month_spend_cents(session, tenant_id) -> int:
    """Sum of this calendar month's AI spend (USD cents) for the tenant."""
    from datetime import datetime, timezone
    from sqlalchemy import func
    from ...models.usage import UsageEvent
    start = datetime.now(timezone.utc).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    total = await session.scalar(
        select(func.coalesce(func.sum(UsageEvent.cost_cents), 0)).where(
            UsageEvent.tenant_id == tenant_id, UsageEvent.created_at >= start)
    )
    return int(total or 0)


def _decrypt(cred: ProviderCredential) -> str | None:
    if not cred.api_key_ciphertext or not cred.api_key_nonce:
        return None
    return get_secret_box().decrypt(cred.api_key_ciphertext, cred.api_key_nonce)


def _mask(cred: ProviderCredential) -> str | None:
    try:
        key = _decrypt(cred)
    except Exception:
        return "••••"
    if not key:
        return None
    return f"••••{key[-4:]}" if len(key) >= 4 else "••••"


class ProviderService:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def resolve(
        self, *, tenant_id: uuid.UUID, task_type: str, override: dict | None = None
    ) -> ResolvedCredential:
        override = override or {}

        # 1) Fully inline override (e.g. an ad-hoc transcription request with its own key).
        if override.get("engine"):
            engine = override["engine"]
            cat = PROVIDER_CATALOG.get(engine)
            if not cat:
                raise HTTPException(status.HTTP_400_BAD_REQUEST, f"unknown engine '{engine}'")
            return ResolvedCredential(
                engine=engine,
                model=override.get("model") or cat["default_model"],
                api_key=override.get("api_key"),
                base_url=override.get("base_url") or cat["default_base_url"],
                params=override.get("params") or {},
            )

        # 2) Named credential, or 3) the tenant's binding for this task type.
        cred: ProviderCredential | None = None
        model = override.get("model")
        params: dict = {}
        if override.get("credential_id"):
            cred = await self.session.get(ProviderCredential, uuid.UUID(str(override["credential_id"])))
            # RLS permits SELECT of both server-scoped and own-tenant creds; guard against a caller
            # passing a tenant credential UUID belonging to a different tenant (would otherwise let
            # them use another tenant's provider key).
            if cred is not None and cred.scope == "tenant" and cred.tenant_id != tenant_id:
                raise HTTPException(status.HTTP_403_FORBIDDEN, "credential belongs to another tenant")
        else:
            binding = await self.session.scalar(
                select(TaskProviderBinding).where(
                    TaskProviderBinding.tenant_id == tenant_id,
                    TaskProviderBinding.task_type == task_type,
                )
            )
            if binding:
                cred = await self.session.get(ProviderCredential, binding.credential_id)
                model = model or binding.model
                params = binding.params or {}

        if not cred:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f"no AI provider configured for '{task_type}' — set a binding or pass an explicit engine",
            )
        if not cred.is_active:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "credential is inactive")

        cat = PROVIDER_CATALOG.get(cred.provider_key, {})
        return ResolvedCredential(
            engine=cred.provider_key,
            model=model or cred.model_default or cat.get("default_model"),
            api_key=_decrypt(cred),
            base_url=cred.base_url or cat.get("default_base_url"),
            params=params,
        )


# ── Management (API-facing) ───────────────────────────────────────────────────


async def create_credential(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    is_server_admin: bool,
    created_by: uuid.UUID,
    scope: str,
    provider_key: str,
    label: str,
    base_url: str | None,
    model_default: str | None,
    api_key: str | None,
) -> ProviderCredential:
    cat = PROVIDER_CATALOG.get(provider_key)
    if not cat:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"unknown provider '{provider_key}'")
    if scope not in ("tenant", "server"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "invalid scope")
    if scope == "server" and not is_server_admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "server credentials require server-admin")
    if cat["requires_key"] and not api_key:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"provider '{provider_key}' requires an API key")

    ciphertext = nonce = None
    if api_key:
        ciphertext, nonce = get_secret_box().encrypt(api_key)

    cred = ProviderCredential(
        scope=scope,
        tenant_id=None if scope == "server" else tenant_id,
        provider_key=provider_key,
        label=label,
        base_url=base_url,
        model_default=model_default,
        api_key_ciphertext=ciphertext,
        api_key_nonce=nonce,
        created_by=created_by,
    )
    session.add(cred)
    await session.flush()
    return cred


async def list_credentials(
    session: AsyncSession, tenant_id: uuid.UUID, is_server_admin: bool
) -> list[ProviderCredential]:
    stmt = select(ProviderCredential).order_by(ProviderCredential.created_at.desc())
    if not is_server_admin:
        # Non-admins manage only their tenant's credentials (RLS still exposes server rows).
        stmt = stmt.where(ProviderCredential.scope == "tenant")
    return list((await session.scalars(stmt)).all())


async def delete_credential(session: AsyncSession, cred_id: uuid.UUID) -> None:
    cred = await session.get(ProviderCredential, cred_id)
    if not cred:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "credential not found")
    await session.delete(cred)  # RLS WITH CHECK governs whether the delete is permitted


async def upsert_binding(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    task_type: str,
    credential_id: uuid.UUID,
    model: str | None,
    params: dict | None,
) -> TaskProviderBinding:
    if task_type not in TASK_CAPABILITY:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "invalid task_type")
    if not await session.get(ProviderCredential, credential_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "credential not found")
    binding = await session.scalar(
        select(TaskProviderBinding).where(
            TaskProviderBinding.tenant_id == tenant_id, TaskProviderBinding.task_type == task_type
        )
    )
    if binding:
        binding.credential_id = credential_id
        binding.model = model
        binding.params = params
    else:
        binding = TaskProviderBinding(
            tenant_id=tenant_id, task_type=task_type, credential_id=credential_id,
            model=model, params=params,
        )
        session.add(binding)
    await session.flush()
    return binding


async def list_bindings(session: AsyncSession, tenant_id: uuid.UUID) -> list[TaskProviderBinding]:
    return list(
        (
            await session.scalars(
                select(TaskProviderBinding).where(TaskProviderBinding.tenant_id == tenant_id)
            )
        ).all()
    )


def masked(cred: ProviderCredential) -> str | None:
    return _mask(cred)
