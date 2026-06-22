from __future__ import annotations

import uuid

from pydantic import BaseModel, EmailStr

from ...models.membership import MembershipRole


class CreateTenantRequest(BaseModel):
    name: str
    slug: str | None = None


class TenantOut(BaseModel):
    id: uuid.UUID
    name: str
    slug: str
    plan: str
    status: str


class AddMemberRequest(BaseModel):
    email: EmailStr
    role: MembershipRole


class MemberOut(BaseModel):
    user_id: uuid.UUID
    email: str
    full_name: str | None
    role: str
