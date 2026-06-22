"""Connector registry: built-ins self-register here; only enabled ones get mounted."""
from __future__ import annotations

from fastapi import FastAPI

from ..settings import Settings
from ..settings import settings as default_settings
from .base import Connector


class ConnectorRegistry:
    def __init__(self) -> None:
        self._connectors: list[Connector] = []

    def register(self, connector: Connector) -> None:
        self._connectors.append(connector)

    def enabled(self, settings: Settings = default_settings) -> list[Connector]:
        return [c for c in self._connectors if c.is_enabled(settings)]

    def mount(self, app: FastAPI, settings: Settings = default_settings) -> list[str]:
        mounted: list[str] = []
        for connector in self.enabled(settings):
            router = connector.router()
            if router is not None:
                app.include_router(router, prefix="/api/connectors")
                mounted.append(connector.name)
        return mounted

    def capabilities(self, settings: Settings = default_settings) -> list[dict]:
        return [c.capabilities() for c in self.enabled(settings)]


registry = ConnectorRegistry()


def discover() -> ConnectorRegistry:
    """Register built-in connectors. Only env-enabled ones are actually mounted."""
    from .builtin.familysearch import FamilySearchConnector

    if not any(c.name == "familysearch" for c in registry._connectors):
        registry.register(FamilySearchConnector())
    return registry
