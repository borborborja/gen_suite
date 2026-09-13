from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.deps import get_current_principal, get_db, get_tenant_db, rate_limit, require_roles
from ...core.security import Principal
from ...models.membership import MembershipRole
from ...settings import settings
from ..tree.audit import audited
from . import service
from .schemas import (
    ProposalIn, ProposalOut, ProposalReview, PublicTreeOut, PublicationCreated, PublicationIn,
    PublicationOut, PublicShareToken,
)

router = APIRouter(tags=["collaboration"])
_PROPOSE = (MembershipRole.contributor.value,)
_REVIEW = (MembershipRole.editor.value,)
_PUBLISH = (MembershipRole.admin.value,)
_public_limit = rate_limit("public_share", limit=60, window_s=60)


def _public_sharing_enabled() -> None:
    if not settings.public_sharing_enabled:
        # Do not disclose whether a formerly public slug exists during an incident.
        raise HTTPException(status.HTTP_404_NOT_FOUND, "enlace no encontrado")


def _private_public_response(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Robots-Tag"] = "noindex, noarchive, nosnippet"


@router.get("/proposals", response_model=list[ProposalOut])
async def list_proposals(status: str | None = Query(default=None), db: AsyncSession = Depends(get_tenant_db)) -> list[ProposalOut]:
    return await service.list_proposals(db, status)


@router.post("/proposals", response_model=ProposalOut, status_code=status.HTTP_201_CREATED,
             dependencies=[Depends(require_roles(*_PROPOSE))])
async def create_proposal(body: ProposalIn, principal: Principal = Depends(get_current_principal),
                          db: AsyncSession = Depends(get_tenant_db)) -> ProposalOut:
    async with audited(db, principal, action="proposal_create", entity_type="proposal", summary="Propuso un cambio"):
        row = await service.create_proposal(db, principal.tenant_id, principal.user_id, body)
    return service.proposal_out(row)


@router.post("/proposals/{proposal_id}/review", response_model=ProposalOut,
             dependencies=[Depends(require_roles(*_REVIEW))])
async def review_proposal(proposal_id: uuid.UUID, body: ProposalReview,
                          principal: Principal = Depends(get_current_principal),
                          db: AsyncSession = Depends(get_tenant_db)) -> ProposalOut:
    async with audited(db, principal, action="proposal_review", entity_type="proposal", entity_id=proposal_id,
                       summary="Revisó una propuesta"):
        row = await service.review_proposal(
            db, proposal_id, principal.user_id, review_status=body.status, reviewer_note=body.reviewer_note
        )
    return service.proposal_out(row)


@router.post("/proposals/{proposal_id}/withdraw", response_model=ProposalOut,
             dependencies=[Depends(require_roles(*_PROPOSE))])
async def withdraw_proposal(proposal_id: uuid.UUID, principal: Principal = Depends(get_current_principal),
                            db: AsyncSession = Depends(get_tenant_db)) -> ProposalOut:
    async with audited(db, principal, action="proposal_withdraw", entity_type="proposal", entity_id=proposal_id,
                       summary="Retiró una propuesta"):
        row = await service.withdraw_proposal(db, proposal_id, principal.user_id)
    return service.proposal_out(row)


@router.get("/publications", response_model=list[PublicationOut])
async def list_publications(db: AsyncSession = Depends(get_tenant_db)) -> list[PublicationOut]:
    return await service.list_publications(db)


@router.post("/publications", response_model=PublicationCreated, status_code=status.HTTP_201_CREATED,
             dependencies=[Depends(require_roles(*_PUBLISH))])
async def create_publication(body: PublicationIn, principal: Principal = Depends(get_current_principal),
                             db: AsyncSession = Depends(get_tenant_db)) -> PublicationCreated:
    async with audited(db, principal, action="publication_create", entity_type="publication", summary="Preparó una publicación privada"):
        return await service.create_publication(db, principal.tenant_id, principal.user_id, body)


@router.post("/publications/{publication_id}/publish", response_model=PublicationOut,
             dependencies=[Depends(require_roles(*_PUBLISH))])
async def publish_publication(publication_id: uuid.UUID, principal: Principal = Depends(get_current_principal),
                              db: AsyncSession = Depends(get_tenant_db)) -> PublicationOut:
    async with audited(db, principal, action="publication_publish", entity_type="publication", entity_id=publication_id,
                       summary="Publicó un árbol compartido"):
        row = await service.publish_publication(db, publication_id)
    return service.publication_out(row)


@router.post("/publications/{publication_id}/refresh", response_model=PublicationOut,
             dependencies=[Depends(require_roles(*_PUBLISH))])
async def refresh_publication(publication_id: uuid.UUID, principal: Principal = Depends(get_current_principal),
                              db: AsyncSession = Depends(get_tenant_db)) -> PublicationOut:
    async with audited(db, principal, action="publication_refresh", entity_type="publication", entity_id=publication_id,
                       summary="Actualizó un árbol compartido"):
        row = await service.refresh_publication(db, publication_id)
    return service.publication_out(row)


@router.post("/publications/{publication_id}/revoke", response_model=PublicationOut,
             dependencies=[Depends(require_roles(*_PUBLISH))])
async def revoke_publication(publication_id: uuid.UUID, principal: Principal = Depends(get_current_principal),
                             db: AsyncSession = Depends(get_tenant_db)) -> PublicationOut:
    async with audited(db, principal, action="publication_revoke", entity_type="publication", entity_id=publication_id,
                       summary="Revocó un árbol compartido"):
        row = await service.revoke_publication(db, publication_id)
    return service.publication_out(row)


@router.post("/public/share/{slug}", response_model=PublicTreeOut,
             dependencies=[Depends(_public_limit), Depends(_public_sharing_enabled)])
async def public_tree(slug: str, body: PublicShareToken, response: Response,
                      db: AsyncSession = Depends(get_db)) -> PublicTreeOut:
    _private_public_response(response)
    row = await service.get_public_tree(db, slug, body.token)
    return PublicTreeOut(title=row.title, published_at=row.published_at, payload=row.payload or {})


@router.get("/public/share/{slug}/{token}", response_model=PublicTreeOut,
            dependencies=[Depends(_public_limit), Depends(_public_sharing_enabled)], include_in_schema=False)
async def legacy_public_tree(slug: str, token: str, response: Response,
                             db: AsyncSession = Depends(get_db)) -> PublicTreeOut:
    """Temporary compatibility endpoint; new links never place a secret in a request URL."""
    _private_public_response(response)
    row = await service.get_public_tree(db, slug, token)
    return PublicTreeOut(title=row.title, published_at=row.published_at, payload=row.payload or {})
