"""Local-only bootstrap: docker compose run --rm gen-suite-backend python -m app.bootstrap_admin.

No HTTP bootstrap window, command-line passwords, promotions or changes to existing
accounts. Uses the same database lock as the historical first-user registration path.
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import getpass
import sys

from pydantic import EmailStr, TypeAdapter, ValidationError
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from .core.security import hash_password
from .db.session import SessionLocal, engine
from .models.user import User
from .settings import settings


class BootstrapError(Exception):
    pass


async def has_admin(session: AsyncSession) -> bool:
    return bool(await session.scalar(select(User.id).where(User.is_server_admin.is_(True), User.is_active.is_(True)).limit(1)))


async def create_initial_admin(session: AsyncSession, email: str, password: str) -> bool:
    email = str(TypeAdapter(EmailStr).validate_python(email)).strip().lower()
    if not 12 <= len(password) <= 128:
        raise BootstrapError("Usa una contraseña de entre 12 y 128 caracteres.")
    # Generate a strong hash before holding the transaction lock.
    password_hash = hash_password(password)
    await session.execute(text("SELECT pg_advisory_xact_lock(hashtext('first_user_admin'))"))
    if await has_admin(session):
        return False
    if await session.scalar(select(User.id).limit(1)):
        raise BootstrapError("Ya existen cuentas sin administración activa. Usa el procedimiento de recuperación; no se promocionan automáticamente.")
    session.add(User(email=email, password_hash=password_hash, is_server_admin=True,
                     is_active=True, email_verified_at=datetime.now(timezone.utc)))
    await session.flush()
    return True


async def bootstrap() -> None:
    try:
        async with SessionLocal() as session:
            if await has_admin(session):
                print("Ya existe una cuenta de administración. No se modifica.")
                return
            if await session.scalar(select(User.id).limit(1)):
                raise BootstrapError("Ya existen cuentas sin administración activa; se requiere recuperación manual.")
        if not sys.stdin.isatty():
            raise BootstrapError("Ejecuta este comando en una terminal para introducir la contraseña sin mostrarla.")
        email = input("Correo de la administración inicial: ").strip()
        password = getpass.getpass("Contraseña (mínimo 12 caracteres): ")
        if password != getpass.getpass("Repite la contraseña: "):
            raise BootstrapError("Las contraseñas no coinciden.")
        async with SessionLocal.begin() as session:
            created = await create_initial_admin(session, email, password)
        print("Administración creada. Ya puedes entrar con tu correo y contraseña." if created else "Otra instalación ya creó la administración; no se modifica.")
    finally:
        await engine.dispose()


def main() -> int:
    parser = argparse.ArgumentParser(description="Crear la primera administración, sin abrir el registro público.")
    parser.add_argument("--validate", action="store_true", help="Validar configuración sin conectar a la base de datos")
    args = parser.parse_args()
    problems = settings.validate_runtime_secrets()
    if problems:
        # Validation messages contain key names, never values.
        print("Configuración incompleta:\n- " + "\n- ".join(problems), file=sys.stderr)
        return 1
    if args.validate:
        print("Configuración válida.")
        return 0
    try:
        asyncio.run(bootstrap())
    except BootstrapError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except ValidationError:
        print("Introduce un correo válido.", file=sys.stderr)
        return 1
    except (KeyboardInterrupt, EOFError):
        print("Creación de cuenta cancelada.", file=sys.stderr)
        return 1
    except Exception:
        # Do not expose SQL parameters, credentials or connection strings on errors.
        print("No se pudo acceder al esquema. Comprueba PostgreSQL y ejecuta las migraciones.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
