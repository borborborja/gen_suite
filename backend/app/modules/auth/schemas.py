from __future__ import annotations

import uuid

from pydantic import BaseModel, EmailStr, Field


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    full_name: str | None = None
    invite_token: str | None = Field(default=None, max_length=512)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str
    mfa_code: str | None = Field(default=None, max_length=64)


class RefreshRequest(BaseModel):
    refresh_token: str


class TokenPair(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class AccessTokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"


class UpdateMeRequest(BaseModel):
    full_name: str | None = None
    email: EmailStr | None = None


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str = Field(min_length=8, max_length=128)


class AccessRequestIn(BaseModel):
    email: EmailStr
    full_name: str | None = Field(default=None, max_length=255)


class AccessRequestOut(BaseModel):
    id: uuid.UUID
    email: str
    full_name: str | None
    status: str


class PasswordResetRequest(BaseModel):
    email: EmailStr


class PasswordResetConfirm(BaseModel):
    token: str = Field(min_length=32, max_length=512)
    new_password: str = Field(min_length=8, max_length=128)


class MfaCodeIn(BaseModel):
    code: str = Field(min_length=6, max_length=64)


class MfaDisableIn(MfaCodeIn):
    password: str = Field(min_length=8, max_length=128)


class MfaEnrollmentOut(BaseModel):
    otpauth_url: str


class MfaConfirmedOut(BaseModel):
    recovery_codes: list[str]


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
