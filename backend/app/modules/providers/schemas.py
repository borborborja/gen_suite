from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel


class ProviderCatalogEntry(BaseModel):
    key: str
    display_name: str
    capabilities: list[str]
    default_base_url: str | None
    default_model: str | None
    requires_key: bool


class CredentialCreate(BaseModel):
    scope: str = "tenant"  # tenant | server
    provider_key: str
    label: str
    base_url: str | None = None
    model_default: str | None = None
    api_key: str | None = None


class CredentialOut(BaseModel):
    id: uuid.UUID
    scope: str
    tenant_id: uuid.UUID | None
    provider_key: str
    label: str
    base_url: str | None
    model_default: str | None
    key_masked: str | None
    is_active: bool
    created_at: datetime


class BindingUpsert(BaseModel):
    task_type: str
    credential_id: uuid.UUID
    model: str | None = None
    params: dict | None = None


class BindingOut(BaseModel):
    id: uuid.UUID
    task_type: str
    credential_id: uuid.UUID
    model: str | None
    params: dict | None
