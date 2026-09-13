"""Conservative admission, independent of the caller's business transaction.

Prices/context ceilings are operator-confirmed, not guessed from model substrings. This
limits application-authorized estimates, not a third party's invoice. Unknown outcomes
retain the entire reservation and stop new paid work until explicitly reconciled.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import uuid
from contextvars import ContextVar
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_CEILING
from typing import Literal
from urllib.parse import urlsplit

from fastapi import HTTPException
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import func, or_, select, update

from ...core import storage
from ...db.rls import set_rls_context
from ...db.session import SessionLocal
from ...models.ai_call import AiCall
from ...models.job import Job
from ...models.research_run import ResearchRun, ResearchRunStage, ResearchRunEvent
from ...models.tenant import Tenant
from ...models.usage import UsageEvent
from . import response_guard

LOCAL_ENGINES = {"tesseract", "kraken", "ollama"}
_ADMITTED: ContextVar[bool] = ContextVar("ai_admitted", default=False)
MAX_RESULT_BYTES = 16 * 1024 * 1024


class BudgetPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model: str = Field(min_length=1, max_length=128)
    # Full provider-enforced context ceiling, including all hidden/image tokens. Reserving
    # the whole window avoids an unverified tokenizer or image-token approximation.
    context_tokens: int = Field(gt=0, le=10_000_000, strict=True)
    max_output_tokens: int = Field(ge=0, le=1_000_000, strict=True)
    input_usd_per_million: Decimal = Field(gt=0, le=10000, max_digits=12, decimal_places=6)
    output_usd_per_million: Decimal = Field(ge=0, le=10000, max_digits=12, decimal_places=6)
    valid_until: AwareDatetime
    source: str = Field(min_length=8, max_length=1000)
    output_parameter: Literal["max_completion_tokens", "max_tokens"] = "max_completion_tokens"
    response_format: Literal["json_schema", "json_object"] = "json_schema"

    @model_validator(mode="after")
    def check_bounds(self):
        if self.max_output_tokens > self.context_tokens:
            raise ValueError("La salida máxima supera la ventana de contexto")
        if self.max_output_tokens and not self.output_usd_per_million:
            raise ValueError("Confirma una tarifa de salida positiva para los modelos generativos")
        return self

    def estimate(self, prompt: int, completion: int) -> int:
        # USD per million tokens == micro-USD per token. Round only once per call.
        return int((prompt * self.input_usd_per_million + completion * self.output_usd_per_million)
                   .to_integral_value(rounding=ROUND_CEILING))

    @property
    def reservation(self) -> int:
        return self.estimate(self.context_tokens, self.max_output_tokens)


def policy_for(rc, *, fresh=True) -> BudgetPolicy:
    try:
        policy = BudgetPolicy.model_validate(rc.params.get("budget_policy"))
    except (ValueError, TypeError):
        raise HTTPException(409, "Configura y confirma la tarifa y los límites del modelo en Ajustes antes de usar IA de pago.") from None
    if policy.model != rc.model:
        raise HTTPException(409, "La tarifa confirmada no corresponde al modelo exacto seleccionado.")
    if rc.engine == "openai" and policy.max_output_tokens and policy.output_parameter != "max_completion_tokens":
        raise HTTPException(409, "Los perfiles OpenAI deben limitar la salida con max_completion_tokens.")
    if fresh and policy.valid_until <= datetime.now(timezone.utc):
        raise HTTPException(409, "La tarifa de IA ha caducado; revísala antes de autorizar nuevas llamadas.")
    return policy


def validate_local(rc) -> None:
    if rc.engine == "tesseract":
        return
    from ...settings import settings
    parsed = urlsplit(rc.base_url or "")
    allowed = {host.strip().lower() for host in settings.ai_local_endpoint_hosts.split(",") if host.strip()}
    if (parsed.scheme not in {"http", "https"} or parsed.hostname not in allowed
            or parsed.username or parsed.password or parsed.query or parsed.fragment or rc.api_key
            or "cloud" in (rc.model or "").lower()):
        raise HTTPException(409, "El motor local requiere un host autorizado por el servidor, sin claves de nube ni modelos cloud.")


def require_admission(engine: str) -> None:
    if engine not in LOCAL_ENGINES and not _ADMITTED.get():
        raise RuntimeError("Llamada de IA bloqueada: falta la reserva duradera de presupuesto.")


def output_options(rc) -> dict:
    if rc.engine in LOCAL_ENGINES:
        return {"max_tokens": 4096}
    policy = policy_for(rc, fresh=False)  # Expiry was checked at durable admission.
    if not policy.max_output_tokens:
        raise HTTPException(409, "El perfil generativo requiere un límite de salida positivo.")
    return {policy.output_parameter: policy.max_output_tokens}


def snapshot(rc) -> dict:
    # Whitelist only; never persist a secret from arbitrary binding params or an endpoint URL.
    if rc.base_url:
        parsed = urlsplit(rc.base_url)
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise HTTPException(409, "La URL del proveedor no puede contener credenciales, parámetros ni fragmentos.")
    return dict(engine=rc.engine, model=rc.model, base_url=rc.base_url,
                credential_id=str(rc.credential_id) if rc.credential_id else None,
                budget_policy=policy_for(rc, fresh=False).model_dump(mode="json"))


def canonical(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")


def fingerprint(value) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


class ReceivedResponseError(ValueError):
    """The provider answered and charged, but its result cannot be applied."""
    def __init__(self, code: str, data, usage: dict):
        super().__init__(code)
        self.code, self.data, self.usage = code, data, usage


def validate_usage(usage, policy: BudgetPolicy) -> int:
    for name in ("prompt", "completion"):
        if type(usage.get(name)) is not int or usage[name] < 0:
            raise ValueError("usage_missing")
    if not usage["prompt"] and not usage["completion"]:
        raise ValueError("usage_missing")
    if usage["prompt"] > policy.context_tokens or usage["completion"] > policy.max_output_tokens:
        raise ValueError("profile_ceiling_exceeded")
    return policy.estimate(usage["prompt"], usage["completion"])


def month_start():
    return datetime.now(timezone.utc).replace(day=1, hour=0, minute=0, second=0, microsecond=0)


async def totals(session, tenant_id) -> dict:
    start = month_start()
    legacy = int(await session.scalar(select(func.coalesce(func.sum(UsageEvent.cost_cents), 0)).where(
        UsageEvent.tenant_id == tenant_id, UsageEvent.created_at >= start)) or 0) * 10000
    # Charge settlement in its UTC month. Reservations cross month boundaries until resolved.
    settled = int(await session.scalar(select(func.coalesce(func.sum(AiCall.cost_microusd), 0)).where(
        AiCall.tenant_id == tenant_id, AiCall.state.in_(("succeeded", "reconciled")),
        AiCall.settled_at >= start)) or 0)
    reserved = int(await session.scalar(select(func.coalesce(func.sum(AiCall.reserved_microusd), 0)).where(
        AiCall.tenant_id == tenant_id, AiCall.state.in_(("inflight", "unknown")))) or 0)
    stale = (AiCall.state == "inflight") & (AiCall.created_at < func.now() - timedelta(minutes=15))
    unknown = int(await session.scalar(select(func.count()).select_from(AiCall).where(
        AiCall.tenant_id == tenant_id, or_(AiCall.state == "unknown", stale))) or 0)
    return dict(settled_microusd=legacy + settled, reserved_microusd=reserved, unknown_calls=unknown)


async def _reserve(rc, profile, request_hash, operation_key, amount):
    async with SessionLocal() as session:
        await set_rls_context(session, tenant_id=rc.tenant_id)
        # All admissions, settlements, reconciliations and budget updates lock this same row.
        # NO KEY UPDATE serializes admissions without deadlocking on the caller's tenant FK
        # KEY SHARE locks (e.g. uncommitted candidate rows in a linkage job).
        tenant = await session.scalar(select(Tenant).where(Tenant.id == rc.tenant_id).with_for_update(key_share=True))
        if tenant is None:
            raise HTTPException(404, "Espacio de trabajo no disponible.")
        previous = await session.scalar(select(AiCall).where(
            AiCall.tenant_id == rc.tenant_id, AiCall.operation_key == operation_key))
        if previous:
            return previous, False
        run = None
        if rc.job_id:
            job_status = await session.scalar(select(Job.status).where(Job.id == rc.job_id,
                                                                         Job.tenant_id == rc.tenant_id))
            if job_status not in {"queued", "running"}:
                raise HTTPException(409, "El trabajo ya no admite nuevas llamadas de IA.")
        if rc.research_run_id:
            from ..research.runs import run_balance
            run = await session.scalar(select(ResearchRun).where(ResearchRun.id == rc.research_run_id,
                ResearchRun.tenant_id == rc.tenant_id).with_for_update())
            if not run or run.status != "running":
                raise HTTPException(409, "La investigación está pausada o finalizada; no se autorizan nuevas llamadas.")
        from .billing import admitted_profile
        profile, amount = await admitted_profile(session, rc, profile, amount)
        if run:
            balance = await run_balance(session, rc.tenant_id, run.id)
            if balance["spent_microusd"] + balance["reserved_microusd"] + amount > run.budget_microusd:
                raise HTTPException(402, "Presupuesto de esta investigación insuficiente para la siguiente reserva.")
        balance = await totals(session, rc.tenant_id)
        if balance["unknown_calls"]:
            raise HTTPException(409, "Hay llamadas de IA con coste incierto; concílialas en Ajustes antes de continuar.")
        limit = tenant.monthly_budget_cents
        if limit is None:
            raise HTTPException(409, "Define un presupuesto mensual explícito antes de autorizar IA de pago.")
        if balance["settled_microusd"] + balance["reserved_microusd"] + amount > limit * 10000:
            raise HTTPException(402, "Presupuesto insuficiente para reservar esta llamada de IA; las reservas activas también cuentan.")
        from ..storage_audit import quota as storage_quota
        call_id = uuid.uuid4()
        response_write = await storage_quota.reserve(session, rc.tenant_id, storage.bucket_for("private"),
            f"{rc.tenant_id}/ai-calls/{call_id}/response.json", MAX_RESULT_BYTES,
            write_id=uuid.uuid5(call_id, "response"))
        response_write.ai_guard_version = 1
        call = AiCall(id=call_id, tenant_id=rc.tenant_id, job_id=rc.job_id, response_write_id=response_write.id,
            research_run_id=rc.research_run_id,
            task_type=rc.task_type or "inference", operation_key=operation_key,
            request_hash=request_hash, profile=profile, state="inflight", reserved_microusd=amount)
        session.add(call)
        await session.commit()  # Independent unit of work; session closes immediately, no RLS reuse.
        return call, True


async def _unknown(tenant_id, call_id, code, *, envelope=None, result_key=None, result_checksum=None):
    async with SessionLocal() as session:
        await set_rls_context(session, tenant_id=tenant_id)
        await session.scalar(select(Tenant.id).where(Tenant.id == tenant_id).with_for_update(key_share=True))
        values = dict(state="unknown", error_code=code)
        if envelope is not None:
            values.update(usage=envelope["usage"], provider_request_id=envelope["usage"].get("request_id"))
        if result_key:
            values.update(result_key=result_key, result_checksum=result_checksum)
        await session.execute(update(AiCall).where(AiCall.id == call_id, AiCall.tenant_id == tenant_id,
            AiCall.state == "inflight").values(**values))
        call = await session.get(AiCall, call_id)
        if call and call.response_write_id:
            from ..storage_audit.quota import cancel_unused
            await cancel_unused(session, tenant_id, call.response_write_id)
        await session.commit()


async def _settle(tenant_id, call_id, envelope, key, checksum, cost):
    async with SessionLocal() as session:
        await set_rls_context(session, tenant_id=tenant_id)
        await session.scalar(select(Tenant.id).where(Tenant.id == tenant_id).with_for_update(key_share=True))
        result = await session.execute(update(AiCall).where(AiCall.id == call_id, AiCall.tenant_id == tenant_id,
            AiCall.state == "inflight").values(state="succeeded", cost_microusd=cost,
            usage=envelope["usage"], result_key=key, result_checksum=checksum,
            provider_request_id=envelope["usage"].get("request_id"), error_code=envelope.get("error"),
            settled_at=datetime.now(timezone.utc)))
        if not result.rowcount:
            raise RuntimeError("La llamada fue conciliada mientras llegaba la respuesta; revisa su contabilidad.")
        await session.commit()


def unpack(envelope, call_id):
    if envelope.get("error"):
        raise ValueError(f"Respuesta de IA no utilizable ({envelope['error']}); consumo conservado, llamada {call_id}.")
    return envelope["data"], {**envelope["usage"], "ai_call_id": str(call_id)}


async def read_saved_response(key: str, checksum: str | None = None) -> tuple[dict, str]:
    obj = await storage.open_object(storage.bucket_for("private"), key)
    try:
        if obj.get("ContentLength", MAX_RESULT_BYTES + 1) > MAX_RESULT_BYTES:
            raise ValueError("result_too_large")
        body = await asyncio.to_thread(obj["Body"].read, MAX_RESULT_BYTES + 1)
    finally:
        await asyncio.to_thread(obj["Body"].close)
    digest = hashlib.sha256(body).hexdigest()
    if len(body) > MAX_RESULT_BYTES or (checksum is not None and digest != checksum):
        raise ValueError("result_checksum_mismatch")
    return json.loads(body), digest


async def attention_for_run(rc, reason):
    if not rc.research_run_id:
        return
    async with SessionLocal() as session:
        await set_rls_context(session, tenant_id=rc.tenant_id)
        await session.scalar(select(Tenant.id).where(Tenant.id == rc.tenant_id).with_for_update(key_share=True))
        run = await session.scalar(select(ResearchRun).where(ResearchRun.id == rc.research_run_id,
            ResearchRun.tenant_id == rc.tenant_id).with_for_update())
        if run and run.status == "running":
            run.status = "needs_attention"; run.error = reason; run.version += 1
            await session.execute(update(Job).where(Job.id == rc.job_id, Job.tenant_id == rc.tenant_id,
                Job.status.in_(("queued", "running"))).values(status="paused"))
            await session.execute(update(ResearchRunStage).where(ResearchRunStage.run_id == run.id,
                ResearchRunStage.job_id == rc.job_id).values(status="needs_attention", error=reason))
            session.add(ResearchRunEvent(tenant_id=rc.tenant_id, run_id=run.id,
                kind="admission_stopped", detail={"job_id": str(rc.job_id), "reason": reason}))
            await session.commit()


async def execute(rc, *, kind: str, request, transport):
    """Exactly one *application* dispatch per fingerprint; no remote exactly-once claim."""
    if rc.engine in LOCAL_ENGINES:
        validate_local(rc)
        return await asyncio.to_thread(transport)
    if not rc.tenant_id:
        raise RuntimeError("La IA de pago requiere contexto de tenant.")
    policy = policy_for(rc, fresh=False)
    profile = snapshot(rc)
    request_hash = fingerprint({"kind": kind, "request": request, "profile": profile})
    operation_key = fingerprint({"request": request_hash, "job": str(rc.job_id) if rc.job_id else "interactive"})
    try:
        call, fresh = await _reserve(rc, profile, request_hash, operation_key, policy.reservation)
    except HTTPException as exc:
        await attention_for_run(rc, str(exc.detail))
        raise
    if not fresh:
        if call.state != "succeeded" or not call.result_key:
            raise HTTPException(409, f"Llamada {call.id} pendiente o incierta. No se repetirá automáticamente.")
        try:
            envelope, _ = await read_saved_response(call.result_key, call.result_checksum)
        except Exception:
            raise HTTPException(409, "No se pudo verificar la respuesta guardada; no se repite la llamada de pago.") from None
        return unpack(envelope, call.id)

    if not call.response_write_id:
        raise HTTPException(409, "Falta la reserva de almacenamiento de la respuesta; no se inicia la llamada de pago.")
    async with response_guard.hold(rc.tenant_id, call.id, call.response_write_id):
        return await _execute_fresh(rc, call, transport)


async def _execute_fresh(rc, call, transport):
    token = _ADMITTED.set(True)
    envelope = None
    stored_key = stored_checksum = None
    try:
        # The receipt fixes the tariff admitted under the tenant lock. The worker's
        # original execution profile must not be rewritten, including on redelivery.
        policy = BudgetPolicy.model_validate(call.profile["budget_policy"])
        try:
            data, usage = await asyncio.to_thread(transport)
            envelope = dict(data=data, usage=usage, error=None)
        except ReceivedResponseError as exc:
            envelope = dict(data=exc.data, usage=exc.usage, error=exc.code)
        body = canonical(envelope)
        if len(body) > MAX_RESULT_BYTES:
            raise ValueError("result_too_large")
        key = f"{rc.tenant_id}/ai-calls/{call.id}/response.json"
        checksum = hashlib.sha256(body).hexdigest()
        await storage.put_object(storage.bucket_for("private"), key, body, "application/json", write_id=call.response_write_id)
        stored_key, stored_checksum = key, checksum
        cost = validate_usage(envelope["usage"], policy)
        await _settle(rc.tenant_id, call.id, envelope, key, checksum, cost)
    except BaseException as exc:
        # Cancellation does not stop a thread/remote request. Leave the reservation intact.
        code = "cancelled_after_admission" if isinstance(exc, asyncio.CancelledError) else "remote_or_persistence_uncertain"
        if isinstance(exc, ValueError) and str(exc) in {"usage_missing", "profile_ceiling_exceeded", "result_too_large"}:
            code = str(exc)
        try:
            await asyncio.shield(_unknown(rc.tenant_id, call.id, code, envelope=envelope,
                                         result_key=stored_key, result_checksum=stored_checksum))
            await asyncio.shield(attention_for_run(rc, "Hay una llamada incierta. Concilia su consumo en Ajustes antes de reanudar."))
        except Exception:
            pass  # The durable inflight reservation still exists; never refund on DB failure.
        if isinstance(exc, asyncio.CancelledError):
            raise
        raise HTTPException(409, f"Resultado o coste incierto en la llamada {call.id}; reserva retenida, sin reintento automático.") from None
    finally:
        _ADMITTED.reset(token)
    return unpack(envelope, call.id)
