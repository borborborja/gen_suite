"""Connector plugin contract.

A connector is an optional integration the *server admin* enables (e.g. FamilySearch).
Gating is defense-in-depth: an env flag (``requires_env_flag``) is the hard gate checked
here; from Phase 6 a server-admin DB toggle can additionally disable an env-enabled
connector at runtime, but can never override a disabled env flag.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Literal

from fastapi import APIRouter

from ..settings import Settings


class Connector(ABC):
    name: str
    scope: Literal["server", "tenant"] = "server"
    requires_env_flag: str | None = None

    def is_enabled(self, settings: Settings) -> bool:
        if self.requires_env_flag:
            return bool(getattr(settings, self.requires_env_flag, False))
        return True

    @abstractmethod
    def router(self) -> APIRouter | None:
        """Return the connector's API router, or None if it exposes no HTTP surface."""

    def capabilities(self) -> dict:
        return {"name": self.name, "scope": self.scope}
