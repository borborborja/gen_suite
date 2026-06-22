#!/usr/bin/env bash
# Supervised ARQ worker. arq exits (rather than reconnecting) when its Redis connection drops —
# e.g. after the laptop sleeps and the host→container port-forward goes stale. Without supervision
# the worker stays dead and every queued job sits at "queued" forever, which looks like a hang in
# the UI. This loop restarts it so the queue always has a consumer.
set -u
cd "$(dirname "$0")"
# shellcheck disable=SC1091
source .venv-local/bin/activate

while true; do
  echo "[run_worker] $(date '+%Y-%m-%d %H:%M:%S') starting arq worker"
  arq app.tasks.worker.WorkerSettings
  code=$?
  echo "[run_worker] $(date '+%Y-%m-%d %H:%M:%S') worker exited (code=$code); restarting in 3s"
  sleep 3
done
