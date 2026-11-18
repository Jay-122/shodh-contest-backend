import os

# Database: Postgres in docker-compose; SQLite file for local runs without Docker.
DATABASE_URL = os.environ.get(
    "DATABASE_URL", "postgresql+psycopg://shodh:shodh@db:5432/shodh"
)

# Judge execution mode: "docker" (enforced in compose) or "local" (subprocess
# fallback used when no Docker daemon is reachable, e.g. dev machines).
JUDGE_MODE = os.environ.get("JUDGE_MODE", "docker")
JUDGE_IMAGE = os.environ.get("JUDGE_IMAGE", "python:3.12-slim")
# Host-visible directory the worker uses for per-run judge workdirs. In compose
# this path is bind-mounted into the worker at the SAME absolute path so the
# Docker daemon can resolve it when spawning sibling judge containers.
JUDGE_HOST_TMP = os.environ.get("JUDGE_HOST_TMP", "/judge_tmp")

WORKER_ID = os.environ.get("WORKER_ID", "worker-1")
POLL_INTERVAL_SECONDS = float(os.environ.get("POLL_INTERVAL_SECONDS", "1.0"))
MAX_JUDGE_RETRIES = int(os.environ.get("MAX_JUDGE_RETRIES", "2"))
OUTPUT_CAP_BYTES = int(os.environ.get("OUTPUT_CAP_BYTES", "1048576"))  # 1 MiB
