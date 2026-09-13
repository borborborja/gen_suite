from __future__ import annotations

import uuid

from pydantic import BaseModel, EmailStr, field_validator

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

    @field_validator("role", mode="before")
    @classmethod
    def normalize_legacy_role(cls, value: object) -> object:
        return {"tenant_admin": "admin", "researcher": "editor"}.get(value, value)


class UpdateMemberRequest(BaseModel):
    role: MembershipRole

    @field_validator("role", mode="before")
    @classmethod
    def normalize_legacy_role(cls, value: object) -> object:
        return {"tenant_admin": "admin", "researcher": "editor"}.get(value, value)


class MemberOut(BaseModel):
    user_id: uuid.UUID
    email: str
    full_name: str | None
    role: str
