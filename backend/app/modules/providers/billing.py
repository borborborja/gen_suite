"""Renew price/expiry independently of the immutable request and vector identities."""
from __future__ import annotations
from datetime import datetime, timezone
from fastapi import HTTPException
from sqlalchemy import select
from ...models.ai_authorization import AiBillingAuthorization
from .admission import BudgetPolicy, fingerprint

SCOPE_FIELDS = {"research_run": "research_run_id", "embedding_generation": "embedding_generation_id", "job": "job_id"}
EXECUTION_FIELDS = ("model", "context_tokens", "max_output_tokens", "output_parameter", "response_format")


def credential_scope(rc):
    if rc.research_run_id: return "research_run", rc.research_run_id
    if getattr(rc, "embedding_generation_id", None): return "embedding_generation", rc.embedding_generation_id
    if rc.job_id: return "job", rc.job_id
    return None, None


async def latest(session, tenant_id, scope, target_id, task_type):
    if scope is None: return None
    return await session.scalar(select(AiBillingAuthorization).where(
        AiBillingAuthorization.tenant_id == tenant_id,
        getattr(AiBillingAuthorization, SCOPE_FIELDS[scope]) == target_id,
        AiBillingAuthorization.task_type == task_type).order_by(AiBillingAuthorization.version.desc()).limit(1))


def compatible_policy(base_profile, renewed):
    try:
        original = BudgetPolicy.model_validate(base_profile["budget_policy"])
        policy = BudgetPolicy.model_validate(renewed)
    except (ValueError, TypeError, KeyError):
        raise HTTPException(409, "La tarifa o sus límites no son válidos. Revisa el perfil y la renovación.") from None
    if any(getattr(original, name) != getattr(policy, name) for name in EXECUTION_FIELDS):
        raise HTTPException(409, "Renovar una tarifa no puede cambiar modelo, límites ni formato de la petición. Prepara otra ejecución para esos cambios.")
    return policy


def require_fresh(policy):
    if policy.valid_until <= datetime.now(timezone.utc):
        raise HTTPException(409, "La autorización económica ha caducado; renueva su tarifa antes de nuevas llamadas.")


async def admitted_profile(session, rc, original, amount):
    """Called while the tenant admission lock is held; never use a worker-cached renewal."""
    scope, target_id = credential_scope(rc)
    authorization = await latest(session, rc.tenant_id, scope, target_id, rc.task_type)
    if authorization is None:
        require_fresh(BudgetPolicy.model_validate(original["budget_policy"]))
        return original, amount
    if authorization.base_profile_hash != fingerprint(original):
        raise HTTPException(409, "La renovación no corresponde al perfil de ejecución fijado.")
    policy = compatible_policy(original, authorization.budget_policy)
    require_fresh(policy)
    return {**original, "budget_policy": policy.model_dump(mode="json"),
            "execution_profile": original,
            "billing_authorization": {"id": str(authorization.id), "version": authorization.version,
                "scope": scope, "target_id": str(target_id), "base_profile_hash": authorization.base_profile_hash}}, policy.reservation
