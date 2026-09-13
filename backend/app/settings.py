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
    invitation_only: bool = False
    frontend_public_url: str = "http://localhost:5173"
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_from: str = ""
    smtp_starttls: bool = True

    # ── Secrets at rest (AES-256-GCM envelope for provider API keys) ──────────
    suite_master_key: str = ""  # base64-encoded 32 bytes; required once Phase 3 lands

    # ── Object storage (MinIO / any S3: AWS, Backblaze, Wasabi) ───────────────
    minio_endpoint: str = "minio:9000"
    minio_access_key: str = "gensuite"
    minio_secret_key: str = "gensuite-secret"
    minio_secure: bool = False
    minio_bucket_private: str = "gensuite-private"
    minio_bucket_public: str = "gensuite-public"
    minio_region: str = "us-east-1"          # AWS needs the bucket's real region for SigV4
    minio_addressing_style: str = ""         # "path" for MinIO/Backblaze; "" = boto3 auto

    # Admission quota for resumable originals, including unfinished manifests.
    # Separate from physical storage: staging and exports need additional headroom.
    ingest_tenant_quota_bytes: int = Field(default=500 * 1024 ** 3, ge=1)
    storage_tenant_quota_bytes: int = Field(default=1024 ** 4, ge=1, le=2**63 - 1)
    ai_local_endpoint_hosts: str = "localhost,127.0.0.1,::1,ollama,kraken-htr"
    ingest_max_files: int = Field(default=100_000, ge=1, le=100_000)

    # Optional local archival derivatives; not manuscript OCR or paid inference.
    pdfa_timeout_seconds: int = Field(default=1800, ge=10, le=7200)
    pdfa_max_pages: int = Field(default=1000, ge=1, le=10000)
    pdfa_max_bytes: int = Field(default=2 * 1024 ** 3, ge=1024, le=16 * 1024 ** 3)
    tree_export_max_rows: int = Field(default=100_000, ge=1, le=1_000_000)
    tree_export_max_graph_bytes: int = Field(default=32 * 1024**2, ge=1024, le=256 * 1024**2)
    tree_export_max_files: int = Field(default=10_000, ge=1, le=100_000)
    tree_export_max_bytes: int = Field(default=2 * 1024**3, ge=1024, le=64 * 1024**3)
    tree_import_max_bytes: int = Field(default=2 * 1024**3, ge=1024, le=64 * 1024**3)
    tree_import_max_expanded_bytes: int = Field(default=4 * 1024**3, ge=1024, le=128 * 1024**3)
    tree_import_max_files: int = Field(default=10_000, ge=1, le=100_000)

    # ── Backups (Postgres → S3) ───────────────────────────────────────────────
    backup_to_s3: bool = False               # enable the daily pg_dump → S3 cron
    backup_retention: int = 14               # legacy hint; retention is enforced by the destination, never deletions here
    backup_hour: int = 3                      # UTC hour for the daily backup
    # A production backup must use a distinct bucket and credentials from the
    # application store, so compromise of the web app cannot erase recovery data.
    backup_s3_endpoint: str = ""
    backup_s3_access_key: str = ""
    backup_s3_secret_key: str = ""
    backup_s3_bucket: str = ""
    backup_s3_region: str = "us-east-1"
    backup_s3_secure: bool = True
    backup_s3_addressing_style: str = ""

    # ── Connector gates (server-admin scope) ──────────────────────────────────
    fs_connector_enabled: bool = False
    gramps_sync_enabled: bool = False
    public_sharing_enabled: bool = True
    api_docs_enabled: bool = True
    app_version: str = "0.2.0"

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
        if self.backup_to_s3 and not self.backup_s3_bucket:
            problems.append("BACKUP_S3_BUCKET is required when BACKUP_TO_S3=true")
        if self.backup_to_s3 and (not self.backup_s3_endpoint or not self.backup_s3_access_key or not self.backup_s3_secret_key):
            problems.append("BACKUP_S3_ENDPOINT/ACCESS_KEY/SECRET_KEY are required when BACKUP_TO_S3=true")
        if self.invitation_only and (not self.smtp_host or not self.smtp_from):
            problems.append("SMTP_HOST and SMTP_FROM are required when INVITATION_ONLY=true")
        return problems


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
