"""Bootstrap integration: only run in the disposable database used by this suite."""
from sqlalchemy import select

from app.bootstrap_admin import BootstrapError, create_initial_admin
from app.core.security import verify_password
from app.db.session import SessionLocal
from app.models.user import User


async def test_local_bootstrap_creates_admin_and_preserves_credentials_on_retry(client):
    async with SessionLocal.begin() as session:
        assert await create_initial_admin(session, "operator@example.org", "initial-secret-123")
    async with SessionLocal.begin() as session:
        assert not await create_initial_admin(session, "other@example.org", "different-secret-123")
    async with SessionLocal() as session:
        users = list((await session.scalars(select(User))).all())
        assert len(users) == 1
        assert users[0].email == "operator@example.org"
        assert users[0].is_server_admin
        assert verify_password("initial-secret-123", users[0].password_hash)
    login = await client.post("/api/auth/session/login", json={"email": "operator@example.org", "password": "initial-secret-123"})
    assert login.status_code == 200, login.text


async def test_bootstrap_refuses_to_promote_existing_accounts(client, monkeypatch):
    from app.settings import settings
    monkeypatch.setattr(settings, "allow_first_user_admin", False)
    registered = await client.post("/api/auth/register", json={"email": "user@example.org", "password": "initial-secret-123"})
    assert registered.status_code == 201
    async with SessionLocal.begin() as session:
        try:
            await create_initial_admin(session, "user@example.org", "replacement-password")
        except BootstrapError:
            pass
        else:
            raise AssertionError("Existing accounts must never be promoted by the installer")
