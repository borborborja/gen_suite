from __future__ import annotations

import uuid

from pydantic import BaseModel, EmailStr, Field


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    full_name: str | None = None


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class RefreshRequest(BaseModel):
    refresh_token: str


class TokenPair(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class UpdateMeRequest(BaseModel):
    full_name: str | None = None
    email: EmailStr | None = None


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str = Field(min_length=8, max_length=128)


class MembershipOut(BaseModel):
    tenant_id: uuid.UUID
    tenant_name: str
    tenant_slug: str
    role: str


class UserOut(BaseModel):
    id: uuid.UUID
    email: str
    full_name: str | None
    is_server_admin: bool


class MeOut(BaseModel):
    user: UserOut
    active_tenant_id: uuid.UUID | None
    active_role: str | None
    memberships: list[MembershipOut]
