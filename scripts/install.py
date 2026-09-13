"""Interactive Docker setup using only the Python standard library.

Never source .env as shell code, display rendered Compose configuration, regenerate
existing secrets, or delete data. Docker is the authority for parsing an existing .env.
"""
from __future__ import annotations

import argparse
import base64
import getpass
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import sys
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parents[1]
STATE_NAME = "installer.json"


class InstallError(Exception):
    pass


def ask(label: str, default: str = "", *, secret: bool = False, optional: bool = False) -> str:
    if not sys.stdin.isatty():
        raise InstallError("Se necesita una terminal interactiva para la primera configuración.")
    while True:
        prompt = f"{label}" + (f" [{default}]" if default else "") + ": "
        value = (getpass.getpass(prompt) if secret else input(prompt)) or default
        if any(ord(char) < 32 for char in value):
            print("Introduce un valor en una sola línea.")
            continue
        if value or optional:
            return value


def validate_url(value: str) -> str:
    parsed = urlsplit(value)
    # The production session uses Secure cookies: a LAN HTTP URL is not a working
    # substitute for HTTPS. Limit input to a hostname before generating proxy syntax.
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
            or parsed.path not in {"", "/"} or parsed.query or parsed.fragment
            or not re.fullmatch(r"[a-zA-Z0-9.-]+", parsed.netloc)
            or ".." in parsed.netloc or parsed.netloc.startswith(('.', '-'))):
        raise InstallError("Usa una URL HTTPS sin ruta ni puerto, por ejemplo https://arbol.example.org.")
    return "https://" + parsed.netloc.lower()


def validate_release(value: str) -> str:
    if not re.fullmatch(r"v?\d+\.\d+\.\d+(?:-[A-Za-z0-9]+(?:[.-][A-Za-z0-9]+)*)?", value):
        raise InstallError("--release requiere una versión publicada concreta, por ejemplo 0.2.0.")
    return value.removeprefix("v")


def dotenv_value(value: str) -> str:
    if any(ord(char) < 32 for char in value):
        raise InstallError("La configuración no admite saltos de línea ni caracteres de control.")
    # Compose treats single quotes literally, including '$' and '#'. Its single-
    # quote parser cannot round-trip all backslash combinations; use escaped double
    # quotes for those values, with '$$' preventing Compose interpolation.
    if "\\" in value:
        return json.dumps(value, ensure_ascii=False).replace("$", "$$")
    return "'" + value.replace("'", "\\'") + "'"


def render_env(template: str, overrides: dict[str, str]) -> str:
    lines, remaining = [], dict(overrides)
    for line in template.splitlines():
        key = line.partition("=")[0]
        if key in remaining:
            lines.append(f"{key}={dotenv_value(remaining.pop(key))}")
        else:
            lines.append(line)
    lines.extend(f"{key}={dotenv_value(value)}" for key, value in remaining.items())
    return "\n".join(lines) + "\n"


def exclusive_write(path: Path, content: str) -> None:
    # O_EXCL also rejects symlinks. Config files are private from their first byte.
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        stream.write(content)


def make_config(root: Path, public_url: str | None, port: int, *, release: str | None = None) -> None:
    config = root / "config"
    target = config / ".env"
    if target.exists() or target.is_symlink():
        raise InstallError("config/.env ya existe; no se sobrescriben configuraciones ni secretos.")
    # Refuse to generate new credentials over data left by a previous installation.
    if (root / "data").exists() and any((root / "data").iterdir()):
        raise InstallError("Hay datos en ./data pero falta config/.env. Restaura su configuración original.")
    url = validate_url(public_url or ask("URL pública HTTPS (proxy o túnel existente)"))
    print("Correo saliente para invitaciones y recuperación de cuenta (SMTP con STARTTLS).")
    smtp_host = ask("Servidor SMTP")
    smtp_port = ask("Puerto SMTP", "587")
    if not smtp_port.isdigit() or not 1 <= int(smtp_port) <= 65535:
        raise InstallError("El puerto SMTP debe estar entre 1 y 65535.")
    smtp_user = ask("Usuario SMTP (vacío para relay sin autenticación)", optional=True)
    smtp_password = ask("Contraseña SMTP", secret=True) if smtp_user else ""
    smtp_from = ask("Remitente", smtp_user if "@" in smtp_user else "")
    overrides = {
        key: secrets.token_hex(32) for key in
        ("POSTGRES_PASSWORD", "APP_DB_PASSWORD", "REDIS_PASSWORD", "MINIO_SECRET_KEY", "JWT_SECRET")
    }
    overrides.update({
        "SUITE_MASTER_KEY": base64.b64encode(secrets.token_bytes(32)).decode("ascii"),
        "ENVIRONMENT": "production", "DEBUG": "false", "ALLOW_FIRST_USER_ADMIN": "false",
        "INVITATION_ONLY": "true", "API_DOCS_ENABLED": "false", "GRAMPS_SYNC_ENABLED": "false",
        "CORS_ORIGINS": json.dumps([url]), "FRONTEND_PUBLIC_URL": url, "FRONTEND_PORT": str(port),
        "SMTP_HOST": smtp_host, "SMTP_PORT": smtp_port, "SMTP_USERNAME": smtp_user,
        "SMTP_PASSWORD": smtp_password, "SMTP_FROM": smtp_from, "SMTP_STARTTLS": "true",
        "DATA_DIR": "./data", "CONFIG_DIR": "./config", "COMPOSE_PROFILES": "bundled-storage",
    })
    if release:
        overrides["GEN_SUITE_VERSION"] = release
    template = (config / ".env.example").read_text(encoding="utf-8")
    content = render_env(template, overrides)
    config.mkdir(exist_ok=True, mode=0o700)
    exclusive_write(target, content)
    caddy = config / "Caddyfile.example"
    if not caddy.exists():
        exclusive_write(caddy, f"{urlsplit(url).hostname} {{\n    reverse_proxy 127.0.0.1:{port}\n}}\n")
    print("Configuración creada en config/.env (0600). Los secretos no se muestran.")


def command_environment(root: Path) -> dict[str, str]:
    # An unrelated exported variable must not override persisted database passwords.
    template = (root / "config/.env.example").read_text(encoding="utf-8")
    keys = set(re.findall(r"^#?([A-Z_][A-Z0-9_]*)=", template, re.MULTILINE))
    keys.update({"COMPOSE_FILE", "COMPOSE_PROJECT_NAME", "COMPOSE_PROFILES", "COMPOSE_ENV_FILES"})
    return {key: value for key, value in os.environ.items() if key not in keys}


def run_command(command: list[str], *, root: Path, env: dict[str, str], label: str,
                capture: bool = False, input_text: str | None = None, timeout: int = 120) -> str:
    try:
        result = subprocess.run(command, cwd=root, env=env, text=True, input=input_text,
                                stdout=subprocess.PIPE if capture else None,
                                stderr=subprocess.PIPE if capture else None, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise InstallError(f"{label}: tiempo agotado. Repite ./install.sh para continuar.") from exc
    except OSError as exc:
        raise InstallError(f"{label}: no se pudo ejecutar el comando.") from exc
    if result.returncode:
        # Config/SQL errors may echo credentials. Do not replay captured stderr/stdout.
        raise InstallError(f"{label}: falló (código {result.returncode}). No se ha borrado ningún dato.")
    return result.stdout or ""


def preflight(root: Path, env: dict[str, str]) -> None:
    if not shutil.which("docker"):
        raise InstallError("Instala Docker Engine y el plugin Compose, o Docker Desktop, antes de continuar.")
    run_command(["docker", "info"], root=root, env=env, capture=True, timeout=15,
                label="Acceso a Docker; comprueba que esté iniciado y tu usuario tenga acceso al daemon")
    version = run_command(["docker", "compose", "version", "--short"], root=root, env=env,
                          capture=True, label="Versión de Docker Compose")
    numbers = re.search(r"(\d+)\.(\d+)", version)
    if not numbers or tuple(map(int, numbers.groups())) < (2, 20):
        raise InstallError("Actualiza Docker Compose a 2.20 o posterior.")
    if shutil.disk_usage(root).free < 5 * 1024**3:
        raise InstallError("Se necesitan al menos 5 GiB libres para preparar la instalación.")


def select_mode(root: Path, args: argparse.Namespace) -> str | None:
    state = root / "config" / STATE_NAME
    requested = validate_release(args.release) if args.release else None
    if state.exists():
        try:
            saved = json.loads(state.read_text(encoding="utf-8"))["release"]
            if saved is not None:
                saved = validate_release(saved)
        except (ValueError, KeyError, TypeError) as exc:
            raise InstallError("config/installer.json no es válido.") from exc
        if (args.release and requested != saved) or (args.build and saved is not None):
            raise InstallError("Esta instalación ya tiene otro modo/versión. Usa el procedimiento de actualización de DEPLOY.md.")
        return saved
    return requested  # No release selected: build the checked-out code (including this installer).


def compose_prefix(root: Path, release: str | None) -> list[str]:
    filename = "compose.release.yaml" if release else "compose.yaml"
    return ["docker", "compose", "--project-directory", str(root), "--env-file", str(root / "config/.env"),
            "-f", str(root / filename)]


def start(root: Path, args: argparse.Namespace) -> None:
    env = command_environment(root)
    if not args.configure_only:
        preflight(root, env)
    release = select_mode(root, args)
    if not (root / "config/.env").exists():
        make_config(root, args.domain and f"https://{args.domain}", args.port, release=release)
    else:
        if args.domain or args.port != 8080:
            raise InstallError("La configuración existente se conserva. Cambia dominio/puerto en config/.env.")
        print("Reutilizando config/.env; se conservan los secretos y las rutas de datos.")
    state = root / "config" / STATE_NAME
    if not state.exists():
        exclusive_write(state, json.dumps({"release": release}, indent=2) + "\n")
    if args.configure_only:
        print("Preparado. Ejecuta ./install.sh para arrancar y crear la administración inicial.")
        return
    if release:
        env["GEN_SUITE_VERSION"] = release
    compose = compose_prefix(root, release)
    execute = lambda tail, **kw: run_command(compose + tail, root=root, env=env, **kw)
    model = json.loads(execute(["config", "--format", "json"], capture=True, label="Validación de Compose"))
    runtime = model["services"]["gen-suite-backend"]["environment"]
    url = validate_url(runtime.get("FRONTEND_PUBLIC_URL", ""))
    if runtime.get("ENVIRONMENT", "").lower() != "production":
        raise InstallError("Este asistente requiere ENVIRONMENT=production en config/.env; usa el arranque manual para desarrollo.")
    if runtime.get("ALLOW_FIRST_USER_ADMIN", "false").lower() == "true":
        raise InstallError("Desactiva ALLOW_FIRST_USER_ADMIN en config/.env: la creación inicial ahora es local.")
    if release:
        print(f"Descargando release {release}…")
        execute(["pull"], label="Descarga de imágenes; verifica que la release esté publicada", timeout=1800)
    else:
        print("Construyendo las imágenes del código local (puede tardar unos minutos)…")
        images = ["gen-suite-backend", "gen-suite-frontend"]
        if "gen-suite-pdfa" in model["services"]:
            images.append("gen-suite-pdfa")
        execute(["build", *images], label="Construcción de imágenes", timeout=3600)
    # Validate configuration before any service/data start, using the actual backend settings.
    execute(["run", "--rm", "--no-deps", "-T", "gen-suite-backend", "python", "-m", "app.bootstrap_admin", "--validate"],
            label="Validación de configuración del backend")
    infrastructure = ["gen-suite-db", "gen-suite-redis"]
    if "gen-suite-minio" in model["services"]:
        infrastructure.append("gen-suite-minio")
    print("Iniciando almacenamiento y cola…")
    execute(["up", "-d", "--wait", "--wait-timeout", str(args.timeout), *infrastructure],
            label="Arranque de almacenamiento", timeout=args.timeout + 30)
    print("Aplicando migraciones…")
    execute(["run", "--rm", "--no-deps", "-T", "gen-suite-migrate"], label="Migraciones", timeout=600)
    print("Comprobando la administración inicial…")
    # A one-off container does not expose HTTP or store the password in its arguments.
    execute(["run", "--rm", "--no-deps", "gen-suite-backend", "python", "-m", "app.bootstrap_admin"],
            label="Creación de la administración inicial", timeout=900)
    print("Iniciando la aplicación…")
    execute(["up", "-d", "--wait", "--wait-timeout", str(args.timeout)],
            label="Arranque de la aplicación", timeout=args.timeout + 30)
    # Check through nginx as well, so an API health check or the SPA fallback alone
    # cannot claim readiness. The JSON response must have the expected status.
    execute(["exec", "-T", "gen-suite-backend", "python", "-c",
             "import json, urllib.request; "
             "assert json.load(urllib.request.urlopen('http://gen-suite-frontend/readyz', timeout=10))['status'] == 'ready'; "
             "urllib.request.urlopen('http://gen-suite-frontend/', timeout=10)"], label="Comprobación de la web")
    print(f"\nAplicación lista en Docker. Acceso: {url}")
    print("Conecta tu proxy/túnel al frontend local; la accesibilidad HTTPS pública aún debe verificarse.")
    if (root / "config/Caddyfile.example").exists():
        print("Ejemplo para Caddy en el host: config/Caddyfile.example.")
    print("Estado: ./install.sh --status  ·  Reiniciar/continuar: ./install.sh")


def main(argv: list[str] | None = None, *, root: Path = ROOT) -> int:
    parser = argparse.ArgumentParser(description="Instalación y arranque guiados de Gen Suite con Docker.")
    parser.add_argument("--domain", help="Dominio HTTPS servido por tu proxy o túnel (sin https://)")
    parser.add_argument("--port", type=int, default=8080, help="Puerto local del frontend (8080)")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--release", help="Descargar una release publicada concreta en lugar de construir")
    mode.add_argument("--build", action="store_true", help="Construir desde el código local (modo inicial por defecto)")
    parser.add_argument("--configure-only", action="store_true", help="Solo generar configuración; no arranca Docker")
    parser.add_argument("--status", action="store_true", help="Mostrar estado, sin modificar ni arrancar servicios")
    parser.add_argument("--timeout", type=int, default=180, help="Espera máxima de salud por fase, en segundos")
    args = parser.parse_args(argv)
    try:
        if not 1024 <= args.port <= 65535 or not 1 <= args.timeout <= 3600:
            raise InstallError("Puerto válido: 1024–65535; espera válida: 1–3600 segundos.")
        if args.status:
            if not (root / "config/.env").exists():
                raise InstallError("Aún no hay configuración. Ejecuta ./install.sh.")
            release = select_mode(root, args)
            env = command_environment(root)
            if release:
                env["GEN_SUITE_VERSION"] = release
            run_command(compose_prefix(root, release) + ["ps", "--all"], root=root, env=env, label="Estado")
        else:
            start(root, args)
        return 0
    except (InstallError, OSError, ValueError) as exc:
        message = str(exc) if isinstance(exc, InstallError) else "No se pudo preparar la instalación. Comprueba permisos y configuración."
        print(f"Error: {message}", file=sys.stderr)
        return 1
    except (EOFError, KeyboardInterrupt):
        print("\nConfiguración interrumpida. Puedes volver a ejecutar ./install.sh.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
