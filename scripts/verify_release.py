"""Fail-closed, read-only GitHub Actions gate. No application config or dependencies."""
from __future__ import annotations

import http.client
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from urllib import error, parse, request

ROOT = Path(__file__).resolve().parents[1]
REQUIRED = {
    "ci.yml": frozenset({"pdfa", "installer", "backend", "frontend"}),
    "recovery.yml": frozenset({"full-recovery"}),
}
MAX_BYTES = 2 * 1024 * 1024
MAX_ITEMS = 100
API_URL = "https://api.github.com"


class GateError(Exception):
    pass


def require(condition, message):
    if not condition:
        raise GateError(message)


def positive_integer(value):
    return type(value) is int and value > 0


class NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise GateError("La API redirige la petición; publicación bloqueada.")


class GitHub:
    def __init__(self, repository, token, api_url=API_URL):
        require(isinstance(repository, str) and re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9_.-]*/[A-Za-z0-9][A-Za-z0-9_.-]*", repository),
            "Repositorio GitHub no válido.")
        require(api_url == API_URL, "Este gate solo admite la API oficial de github.com.")
        require(isinstance(token, str) and token and not any(c.isspace() for c in token),
                "Falta un token válido de solo lectura para Actions.")
        self.repository = repository
        self.token = token
        # Never forward the bearer token through redirects or environment proxy settings.
        self.opener = request.build_opener(request.ProxyHandler({}), NoRedirect())

    def get(self, path, **query):
        require(re.fullmatch(r"actions/[A-Za-z0-9_./-]+", path) and ".." not in path,
                "Ruta de API no válida.")
        url = f"{API_URL}/repos/{self.repository}/{path}"
        if query:
            url += "?" + parse.urlencode(query)
        req = request.Request(url, headers={
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "gen-suite-release-gate",
        })
        try:
            with self.opener.open(req, timeout=20) as response:
                require(response.status == 200, "Respuesta HTTP no válida de la API.")
                raw = response.read(MAX_BYTES + 1)
            require(len(raw) <= MAX_BYTES, "Respuesta de API demasiado grande.")
            data = json.loads(raw)
        except (error.URLError, OSError, http.client.HTTPException, ValueError):
            # Do not echo bodies, URLs, exception text, headers or credentials.
            raise GateError("No se pudo verificar la API de GitHub; publicación bloqueada.") from None
        require(isinstance(data, dict), "Respuesta JSON no válida de la API.")
        return data


def complete_list(data, field):
    items = data.get(field)
    count = data.get("total_count")
    # An incomplete page must never hide a newer failed/pending run. More than 100
    # matches requires operator investigation, not a permissive pagination fallback.
    require(type(count) is int and 0 <= count <= MAX_ITEMS and isinstance(items, list)
            and len(items) == count and all(isinstance(item, dict) for item in items),
            "Lista de API incompleta o fuera de límite; publicación bloqueada.")
    return items


def successful(item):
    return item.get("status") == "completed" and item.get("conclusion") == "success"


def latest_run(api, filename, workflow_id, sha):
    data = api.get(f"actions/workflows/{filename}/runs", head_sha=sha, branch="main",
                   event="push", per_page=MAX_ITEMS, page=1)
    runs = complete_list(data, "workflow_runs")
    require(runs, f"{filename}: falta una ejecución push de main para el commit exacto.")
    for run in runs:
        require(all(positive_integer(run.get(key)) for key in
                    ("id", "run_number", "run_attempt", "workflow_id")),
                f"{filename}: identidad de ejecución no válida.")
        require(run["workflow_id"] == workflow_id and run.get("head_sha") == sha
                and run.get("event") == "push" and run.get("head_branch") == "main"
                and all(isinstance(run.get(key), dict) and
                        isinstance(run[key].get("full_name"), str) and
                        run[key]["full_name"].casefold() == api.repository.casefold()
                        for key in ("repository", "head_repository")),
                f"{filename}: ejecución de otro commit, repositorio, rama o evento.")
    require(len({run["id"] for run in runs}) == len(runs)
            and len({run["run_number"] for run in runs}) == len(runs),
            f"{filename}: ejecuciones duplicadas o ambiguas.")
    # Do not filter by success/completed: that would allow an older green run to
    # conceal a newer queued, failed, cancelled or currently rerunning validation.
    latest = max(runs, key=lambda run: run["run_number"])
    require(successful(latest), f"{filename}: la última ejecución no ha terminado correctamente.")
    return latest


def verify(api, sha):
    require(isinstance(sha, str) and re.fullmatch(r"[0-9a-f]{40}", sha),
            "Se requiere el SHA completo e inmutable del commit.")
    evidence = []
    for filename, required_jobs in REQUIRED.items():
        workflow = api.get(f"actions/workflows/{filename}")
        require(positive_integer(workflow.get("id")) and workflow.get("state") == "active"
                and workflow.get("path") == f".github/workflows/{filename}",
                f"{filename}: workflow obligatorio ausente, desactivado o distinto.")
        run = latest_run(api, filename, workflow["id"], sha)
        # Address the attempt explicitly; never mix a failed re-run with old jobs.
        jobs = complete_list(api.get(
            f"actions/runs/{run['id']}/attempts/{run['run_attempt']}/jobs",
            per_page=MAX_ITEMS, page=1), "jobs")
        names = [job.get("name") for job in jobs]
        require(all(isinstance(name, str) for name in names), f"{filename}: jobs no válidos.")
        require(required_jobs.issubset(names) and len(names) == len(set(names)),
                f"{filename}: faltan jobs obligatorios o hay duplicados; reejecutar todos los jobs.")
        require(all(successful(job) and type(job.get("run_id")) is int
                    and job["run_id"] == run["id"] and job.get("head_sha") == sha
                    for job in jobs),
                f"{filename}: un job está omitido, incompleto, fallido o corresponde a otro commit.")
        evidence.append((filename, workflow["id"], run))
    # Recheck after fetching both sets of jobs, catching intervening re-runs/new runs.
    for filename, workflow_id, run in evidence:
        current = latest_run(api, filename, workflow_id, sha)
        require(all(current[key] == run[key] for key in ("id", "run_number", "run_attempt")),
                f"{filename}: la ejecución cambió durante la verificación; repetir el gate.")
    return [(filename, run["id"], run["run_attempt"]) for filename, _, run in evidence]


def main():
    try:
        require(os.environ.get("GITHUB_ACTIONS") == "true" and
                os.environ.get("GITHUB_EVENT_NAME") in {"release", "workflow_dispatch"},
                "Ejecutar este gate desde el workflow de publicación de GitHub Actions.")
        sha = os.environ.get("GITHUB_SHA", "")
        require(re.fullmatch(r"[0-9a-f]{40}", sha), "GITHUB_SHA no es un commit completo.")
        try:
            checked_out = subprocess.run(
                ["git", "rev-parse", "--verify", "HEAD"], cwd=ROOT, check=True,
                capture_output=True, text=True, timeout=10).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            raise GateError("No se pudo identificar el commit descargado.") from None
        require(checked_out == sha, "El checkout no corresponde al commit del evento.")
        require(os.environ.get("GITHUB_OUTPUT"), "Falta el canal de salida de GitHub Actions.")
        api = GitHub(os.environ.get("GITHUB_REPOSITORY", ""), os.environ.get("GH_TOKEN", ""),
                     os.environ.get("GITHUB_API_URL", ""))
        evidence = verify(api, sha)
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as output:
            output.write(f"sha={sha}\n")
        print(f"Gate correcto para {sha}.")
        for filename, run_id, attempt in evidence:
            print(f"{filename}: https://github.com/{api.repository}/actions/runs/{run_id}/attempts/{attempt}")
        return 0
    except (GateError, OSError) as exc:
        message = str(exc) if isinstance(exc, GateError) else "No se pudo escribir la evidencia del gate."
        print(f"Publicación bloqueada: {message}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
