"""Application settings, loaded from environment / .env.

Two database roles are used (see db/ and alembic/):
  * the *admin* role (POSTGRES_USER) owns the schema and runs migrations; it is a
    superuser in the dev image and is used ONLY by Alembic.
  * the *app* role (APP_DB_USER) is a NON-superuser, NOBYPASSRLS login role that the
    FastAPI app and the ARQ worker connect with, so Row-Level Security is enforced.
"""
from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore", case_sensitive=False
    )

    environment: str = "development"
    debug: bool = False

    # ── Postgres ────────────────────────────────────────────────────────────
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_db: str = "gensuite"
    # admin / owner role (migrations only)
    postgres_user: str = "gensuite"
    postgres_password: str = "gensuite"
    # application role (runtime; non-superuser so RLS applies)
    app_db_user: str = "gensuite_app"
    app_db_password: str = "gensuite_app"

    # ── Redis (ARQ queue + pub/sub for SSE) ───────────────────────────────────
    redis_url: str = "redis://localhost:6379/0"

    # ── Auth / JWT ────────────────────────────────────────────────────────────
    jwt_secret: str = "dev-insecure-change-me"
    jwt_algorithm: str = "HS256"
    access_token_ttl_minutes: int = 15
    refresh_token_ttl_days: int = 30

    # ── Secrets at rest (AES-256-GCM envelope for provider API keys) ──────────
    suite_master_key: str = ""  # base64-encoded 32 bytes; required once Phase 3 lands

    # ── Object storage (MinIO / S3) — used from Phase 2 ───────────────────────
    minio_endpoint: str = "minio:9000"
    minio_access_key: str = "gensuite"
    minio_secret_key: str = "gensuite-secret"
    minio_secure: bool = False
    minio_bucket_private: str = "gensuite-private"
    minio_bucket_public: str = "gensuite-public"

    # ── Connector gates (server-admin scope) ──────────────────────────────────
    fs_connector_enabled: bool = False

    # First registered user is auto-promoted to server admin only when this is on (bootstrap).
    # Off by default so an internet-exposed fresh deploy can't be admin-claimed by a stranger.
    allow_first_user_admin: bool = False

    # ── HTTP ──────────────────────────────────────────────────────────────────
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:5173"])

    def _pg_url(self, user: str, password: str, *, driver: str = "postgresql+asyncpg") -> str:
        return f"{driver}://{user}:{password}@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"

    @property
    def async_database_url(self) -> str:
        """Runtime connection as the restricted app role (RLS enforced)."""
        return self._pg_url(self.app_db_user, self.app_db_password)

    @property
    def admin_database_url(self) -> str:
        """Migration connection as the owner/superuser role."""
        return self._pg_url(self.postgres_user, self.postgres_password)

    def validate_runtime_secrets(self) -> list[str]:
        """Outside development, refuse to run with placeholder/example secrets — these are
        publicly known and would allow token forgery / plaintext-equivalent credential storage.
        Returns a list of problems (empty == OK)."""
        if self.environment.lower() == "development":
            return []
        import base64

        problems: list[str] = []
        if "insecure" in self.jwt_secret.lower() or len(self.jwt_secret) < 32:
            problems.append("JWT_SECRET is the dev placeholder or too short (need ≥32 random chars)")
        try:
            mk = base64.b64decode(self.suite_master_key) if self.suite_master_key else b""
        except Exception:
            mk = b""
        if len(mk) != 32 or mk == b"\x00" * 32:
            problems.append("SUITE_MASTER_KEY must be base64 of 32 random bytes (not empty/all-zero)")
        weak_pw = {"gensuite", "gensuite_app", "gensuite-secret", "gensuite_dev",
                   "gensuite_app_dev", "gensuite_dev_secret"}
        if self.postgres_password in weak_pw or self.app_db_password in weak_pw:
            problems.append("Postgres/app DB password is a known example value")
        if self.minio_secret_key in weak_pw:
            problems.append("MINIO_SECRET_KEY is a known example value")
        return problems


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
