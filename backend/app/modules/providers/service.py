"""AI provider registry: per-task credential resolution + credential/binding management.

``ProviderService.resolve`` follows a ``cfg or binding or catalog-default`` chain so
transcription, embeddings and inference all pick a provider/model/key the same way.
The decrypted key is returned only to in-process callers (workers), never via the API.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field

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
    api_key: str | None = field(repr=False)
    base_url: str | None
    params: dict
    tenant_id: uuid.UUID | None = None
    credential_id: uuid.UUID | None = None
    job_id: uuid.UUID | None = None
    task_type: str | None = None
    research_run_id: uuid.UUID | None = None
    embedding_generation_id: uuid.UUID | None = None

    def to_engine_kwargs(self) -> dict:
        return {
            "engine": self.engine,
            "model": self.model,
            "api_key": self.api_key,
            "base_url": self.base_url,
        }


EMBEDDING_DIM = 1024


async def embed_texts(rc: ResolvedCredential, texts: list[str]) -> list[list[float]]:
    """Admitted async embeddings, standardized on 1024 dimensions."""
    vectors, _ = await embed_texts_with_usage(rc, texts)
    return vectors


async def embed_texts_with_usage(
    rc: ResolvedCredential, texts: list[str]
) -> tuple[list[list[float]], dict]:
    from .admission import execute, LOCAL_ENGINES, policy_for
    if rc.engine not in {"openai", "ollama", "jina"}:
        raise HTTPException(409, "El proveedor seleccionado no admite embeddings en esta integración.")
    if not texts:
        return [], {"prompt": 0, "completion": 0}
    if rc.engine not in LOCAL_ENGINES:
        # Bound the aggregate batch, not each individual string, by a conservative UTF-8
        # byte ceiling. Reserve the full confirmed window, even for very short queries.
        policy = policy_for(rc, fresh=False)
        if sum(len(t.encode("utf-8")) + 32 for t in texts) > policy.context_tokens:
            raise HTTPException(413, "Divide el lote de embeddings: supera el límite conservador del perfil.")
    return await execute(rc, kind="embedding", request=texts,
                         transport=lambda: _embed_texts_transport(rc, texts))


def _embed_texts_transport(
    rc: ResolvedCredential, texts: list[str]
) -> tuple[list[list[float]], dict]:
    """Like ``embed_texts`` but also returns token usage ``{prompt, completion}`` for the
    spending control. Sync — call from a thread."""
    from openai import OpenAI
    from .admission import ReceivedResponseError, require_admission

    require_admission(rc.engine)

    if rc.engine not in ("openai", "ollama", "jina"):
        raise ValueError(f"engine '{rc.engine}' does not support embeddings")
    # Bound each call so a throttled/hung provider fails fast instead of blocking the worker job
    # (which now has a multi-hour job_timeout). A failed page is handled per-page upstream.
    client = OpenAI(api_key=rc.api_key or "none", base_url=rc.base_url, timeout=60.0, max_retries=0)
    kwargs: dict = {"model": rc.model, "input": texts}
    if rc.engine in ("openai", "jina"):  # both accept an explicit output dimensionality
        kwargs["dimensions"] = EMBEDDING_DIM
    resp = client.embeddings.create(**kwargs)
    ordered = sorted(resp.data, key=lambda item: item.index)
    vectors = [d.embedding for d in ordered]
    u = getattr(resp, "usage", None)
    usage = {"prompt": getattr(u, "prompt_tokens", None), "completion": 0,
             "request_id": getattr(resp, "_request_id", None)}
    if [item.index for item in ordered] != list(range(len(texts))) or any(len(v) != EMBEDDING_DIM for v in vectors):
        raise ReceivedResponseError("invalid_embedding_shape", vectors, usage)
    return vectors, usage


async def extract_structured_with_usage(
    rc: ResolvedCredential, text: str, *, schema: dict, system: str, schema_name: str = "extraction"
) -> tuple[dict, dict]:
    from .admission import execute, canonical, LOCAL_ENGINES, policy_for
    request = dict(text=text, schema=schema, system=system, schema_name=schema_name)
    if rc.engine not in LOCAL_ENGINES:
        policy = policy_for(rc, fresh=False)
        if not policy.max_output_tokens:
            raise HTTPException(409, "La extracción necesita un perfil con salida máxima positiva.")
        if len(canonical(request)) + 1024 + policy.max_output_tokens > policy.context_tokens:
            raise HTTPException(413, "El texto y la salida reservada superan el límite conservador del perfil.")
    return await execute(rc, kind="structured", request=request,
        transport=lambda: _extract_structured_transport(rc, text, schema=schema, system=system, schema_name=schema_name))


def _extract_structured_transport(
    rc: ResolvedCredential, text: str, *, schema: dict, system: str, schema_name: str = "extraction"
) -> tuple[dict, dict]:
    """Like ``extract_structured`` but also returns token usage ``{prompt, completion, total}`` for
    the M3 cost probe (plan §M3). Sync — call from a thread."""
    import json

    from openai import OpenAI
    from .admission import LOCAL_ENGINES, ReceivedResponseError, output_options, policy_for, require_admission

    require_admission(rc.engine)
    if rc.engine == "claude":
        return _extract_anthropic_transport(rc, text, schema=schema, system=system)

    if rc.engine == "tesseract":
        raise ValueError("engine 'tesseract' cannot do inference")
    # Per-call timeout so a throttled/hung provider fails fast (the page is retried later via the
    # extraction anti-join) instead of stalling the whole job under the multi-hour job_timeout.
    client = OpenAI(api_key=rc.api_key or "none", base_url=rc.base_url, timeout=90.0, max_retries=0)
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": text},
    ]
    response_format = {
        "type": "json_schema",
        "json_schema": {"name": schema_name, "schema": schema, "strict": False},
    }
    if rc.engine not in LOCAL_ENGINES and policy_for(rc, fresh=False).response_format == "json_object":
        response_format = {"type": "json_object"}
    resp = client.chat.completions.create(
        model=rc.model, messages=messages, response_format=response_format,
        n=1, **output_options(rc),
    )
    u = getattr(resp, "usage", None)
    usage = {
        "prompt": getattr(u, "prompt_tokens", None),
        "completion": getattr(u, "completion_tokens", None),
        "total": getattr(u, "total_tokens", 0) or 0,
        "request_id": getattr(resp, "_request_id", None),
    }
    content = resp.choices[0].message.content if resp.choices else None
    if not content or resp.choices[0].finish_reason != "stop":
        raise ReceivedResponseError("incomplete_or_refused", content, usage)
    try:
        data = json.loads(content)
    except ValueError:
        raise ReceivedResponseError("invalid_json", content, usage) from None
    return data, usage


def _extract_anthropic_transport(rc, text, *, schema, system):
    """Native Anthropic transport: never send an Anthropic key to the OpenAI default URL."""
    import json
    import anthropic
    from .admission import ReceivedResponseError, policy_for, require_admission
    require_admission("claude")
    response = anthropic.Anthropic(api_key=rc.api_key, base_url=rc.base_url, timeout=90.0, max_retries=0).messages.create(
        model=rc.model, max_tokens=policy_for(rc, fresh=False).max_output_tokens,
        system=system + "\nDevuelve exclusivamente JSON conforme a este esquema:\n" + json.dumps(schema, ensure_ascii=False),
        messages=[{"role": "user", "content": text}],
    )
    u = response.usage
    prompt = getattr(u, "input_tokens", None)
    if prompt is not None:
        prompt += (getattr(u, "cache_creation_input_tokens", 0) or 0) + (getattr(u, "cache_read_input_tokens", 0) or 0)
    usage = {"prompt": prompt, "completion": getattr(u, "output_tokens", None),
             "request_id": getattr(response, "_request_id", None)}
    content = "".join(block.text for block in response.content if getattr(block, "type", None) == "text")
    if not content or response.stop_reason != "end_turn":
        raise ReceivedResponseError("incomplete_or_refused", content, usage)
    try:
        return json.loads(content), usage
    except ValueError:
        raise ReceivedResponseError("invalid_json", content, usage) from None


async def extract_structured(
    rc: ResolvedCredential, text: str, *, schema: dict, system: str, schema_name: str = "extraction"
) -> dict:
    """Admitted async text→JSON extraction, with durable usage and result replay."""
    data, _ = await extract_structured_with_usage(
        rc, text, schema=schema, system=system, schema_name=schema_name
    )
    return data


# ── Batch API (async, ~50% cheaper) over the OpenAI-compatible /batches protocol ──────────────
# Works with providers that expose OpenAI's Files+Batches endpoints (OpenAI; Google Gemini's batch).
# OpenRouter has no batch endpoint, so the task falls back to sync for it. Sync helpers — call from
# a thread.

def _client(rc: ResolvedCredential, timeout: float = 120.0):
    from openai import OpenAI
    return OpenAI(api_key=rc.api_key or "none", base_url=rc.base_url, timeout=timeout, max_retries=0)


def submit_chat_batch(
    rc: ResolvedCredential, items: list[tuple[str, str]], *, system: str
) -> str:
    """Fail closed until Batch has per-request durable admission/reconciliation."""
    raise HTTPException(409, "Nuevos lotes Batch desactivados hasta disponer de reserva y conciliación por solicitud. Usa el modo síncrono gestionado.")


def poll_batch(rc: ResolvedCredential, batch_id: str) -> tuple[str, str | None]:
    """Returns (status, output_file_id). status ∈ validating|in_progress|finalizing|completed|
    failed|expired|cancelled."""
    b = _client(rc).batches.retrieve(batch_id)
    return b.status, getattr(b, "output_file_id", None)


def fetch_batch_results(
    rc: ResolvedCredential, output_file_id: str
) -> tuple[dict[str, dict | None], dict]:
    """Map each custom_id → its parsed JSON body (None if that line errored), plus the aggregated
    token usage ``{prompt, completion, total}`` across all lines (for the spending control)."""
    import json as _json

    text = _client(rc).files.content(output_file_id).text
    out: dict[str, dict | None] = {}
    usage = {"prompt": 0, "completion": 0, "total": 0}
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        obj = None
        try:
            obj = _json.loads(line)
            body = (obj.get("response") or {}).get("body") or {}
            u = body.get("usage") or {}
            usage["prompt"] += int(u.get("prompt_tokens") or 0)
            usage["completion"] += int(u.get("completion_tokens") or 0)
            usage["total"] += int(u.get("total_tokens") or 0)
            content = body["choices"][0]["message"]["content"]
            out[obj["custom_id"]] = _json.loads(content)
        except Exception:
            cid = obj.get("custom_id") if isinstance(obj, dict) else None
            if cid:
                out[cid] = None
    return out, usage


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
    from .admission import totals
    amount = (await totals(session, tenant_id))["settled_microusd"]
    return (amount + 9999) // 10000


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

    async def resolve_frozen(self, *, tenant_id, task_type, profile, job_id=None, research_run_id=None, embedding_generation_id=None):
        credential_id = uuid.UUID(profile["credential_id"]) if profile.get("credential_id") else None
        cred = await self.session.get(ProviderCredential, credential_id) if credential_id else None
        if credential_id and (not cred or not cred.is_active or (cred.scope != "server" and cred.tenant_id != tenant_id)):
            raise HTTPException(409, "La credencial del perfil fijado ya no está disponible.")
        return _require_known_tariff(ResolvedCredential(engine=profile["engine"], model=profile["model"],
            base_url=profile["base_url"], api_key=_decrypt(cred) if cred else None,
            params={"budget_policy": profile["budget_policy"]} if "budget_policy" in profile else {},
            tenant_id=tenant_id, credential_id=credential_id, task_type=task_type,
            job_id=job_id, research_run_id=research_run_id, embedding_generation_id=embedding_generation_id))

    async def resolve(
        self, *, tenant_id: uuid.UUID, task_type: str, override: dict | None = None,
        job_id: uuid.UUID | None = None,
    ) -> ResolvedCredential:
        override = override or {}
        from ...core.execution_context import current_job
        context = current_job.get()
        if job_id is None and context and context[0] == tenant_id:
            job_id = context[1]
        if job_id:
            from ...models.job import Job
            from ...models.research_run import ResearchRun
            job = await self.session.scalar(select(Job).where(Job.id == job_id, Job.tenant_id == tenant_id))
            run_id = (job.params or {}).get("research_run_id") if job else None
            if run_id:
                run = await self.session.scalar(select(ResearchRun).where(
                    ResearchRun.id == uuid.UUID(run_id), ResearchRun.tenant_id == tenant_id))
                if not run or task_type not in run.profiles:
                    raise HTTPException(409, "La investigación no autoriza un proveedor para esta etapa.")
                frozen = run.profiles[task_type]
                credential_id = uuid.UUID(frozen["credential_id"]) if frozen.get("credential_id") else None
                cred = await self.session.get(ProviderCredential, credential_id) if credential_id else None
                if credential_id and (not cred or not cred.is_active or (cred.scope != "server" and cred.tenant_id != tenant_id)):
                    raise HTTPException(409, "La credencial congelada de la investigación ya no está disponible.")
                resolved = ResolvedCredential(engine=frozen["engine"], model=frozen["model"], base_url=frozen["base_url"],
                    api_key=_decrypt(cred) if cred else None,
                    params={"budget_policy": frozen["budget_policy"]} if "budget_policy" in frozen else {},
                    tenant_id=tenant_id, credential_id=credential_id, job_id=job_id, task_type=task_type,
                    research_run_id=run.id)
                return _require_known_tariff(resolved)
            generation_id = (job.params or {}).get("index_generation_id") if job else None
            if generation_id and task_type == "embedding":
                from ...models.embedding_index import EmbeddingGeneration
                generation = await self.session.scalar(select(EmbeddingGeneration).where(
                    EmbeddingGeneration.id == uuid.UUID(generation_id), EmbeddingGeneration.tenant_id == tenant_id))
                if not generation: raise HTTPException(409, "Generación de búsqueda no disponible.")
                return await self.resolve_frozen(tenant_id=tenant_id, task_type=task_type, profile=generation.profile,
                    job_id=job_id, embedding_generation_id=generation.id)
        # Re-delivery after the first admitted call uses its frozen public profile; only the
        # active credential's secret is resolved again. No key ever enters a job or ledger row.
        if job_id:
            from ...models.ai_call import AiCall
            previous = await self.session.scalar(select(AiCall).where(
                AiCall.tenant_id == tenant_id, AiCall.job_id == job_id, AiCall.task_type == task_type,
            ).order_by(AiCall.created_at, AiCall.id).limit(1))
            if previous:
                frozen = previous.profile.get("execution_profile", previous.profile)
                cred = await self.session.get(ProviderCredential, uuid.UUID(frozen["credential_id"])) if frozen.get("credential_id") else None
                if not cred or not cred.is_active or (cred.scope != "server" and cred.tenant_id != tenant_id):
                    raise HTTPException(409, "La credencial del perfil congelado ya no está disponible.")
                return ResolvedCredential(engine=frozen["engine"], model=frozen["model"],
                    api_key=_decrypt(cred), base_url=frozen["base_url"],
                    params={"budget_policy": frozen["budget_policy"]}, tenant_id=tenant_id,
                    credential_id=cred.id, job_id=job_id, task_type=task_type)

        # 1) Fully inline override (e.g. an ad-hoc transcription request with its own key).
        if override.get("engine") and not override.get("credential_id"):
            engine = override["engine"]
            cat = PROVIDER_CATALOG.get(engine)
            if not cat:
                raise HTTPException(status.HTTP_400_BAD_REQUEST, f"unknown engine '{engine}'")
            resolved = ResolvedCredential(
                engine=engine,
                model=override.get("model") or cat["default_model"],
                api_key=override.get("api_key"),
                base_url=override.get("base_url") or cat["default_base_url"],
                params=override.get("params") or {},
                tenant_id=tenant_id, job_id=job_id, task_type=task_type,
            )
            return _require_known_tariff(resolved)

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
            binding = await self.session.scalar(select(TaskProviderBinding).where(
                TaskProviderBinding.tenant_id == tenant_id, TaskProviderBinding.task_type == task_type,
                TaskProviderBinding.credential_id == cred.id if cred else False,
            ))
            if binding:
                params = binding.params or {}
                model = model or binding.model
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
        resolved = ResolvedCredential(
            engine=cred.provider_key,
            model=model or cred.model_default or cat.get("default_model"),
            api_key=_decrypt(cred),
            base_url=cred.base_url or cat.get("default_base_url"),
            params=params,
            tenant_id=tenant_id, credential_id=cred.id, job_id=job_id, task_type=task_type,
        )
        return _require_known_tariff(resolved)


def _require_known_tariff(resolved: ResolvedCredential) -> ResolvedCredential:
    """Require an exact, operator-confirmed profile; never infer a tariff by substring."""
    from .admission import LOCAL_ENGINES, policy_for, validate_local
    if resolved.engine not in LOCAL_ENGINES:
        if not resolved.credential_id:
            raise HTTPException(409, "Guarda la credencial y vincula una tarifa confirmada en Ajustes; no se admiten claves de pago inline.")
        policy_for(resolved, fresh=False)
    else:
        validate_local(resolved)
    return resolved


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
    credential = await session.get(ProviderCredential, credential_id)
    if not credential:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "credential not found")
    if credential.scope != "server" and credential.tenant_id != tenant_id:
        raise HTTPException(403, "La credencial pertenece a otro espacio de trabajo.")
    if params and "budget_policy" in params:
        from .admission import BudgetPolicy, policy_for
        from pydantic import ValidationError
        try:
            BudgetPolicy.model_validate(params["budget_policy"])
        except ValidationError:
            raise HTTPException(422, "La tarifa o los límites del perfil de IA no son válidos.") from None
        policy_for(ResolvedCredential(engine=credential.provider_key,
            model=model or credential.model_default or PROVIDER_CATALOG.get(credential.provider_key, {}).get("default_model"),
            base_url=credential.base_url, api_key=None, params=params))
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
