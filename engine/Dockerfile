# syntax=docker/dockerfile:1
# Scaffold engine image — slim, non-root, healthchecked.
# Build (repo root):  docker build -t scaffold-engine ./engine
# Run:                docker run --env-file engine/.env -p 8000:8000 scaffold-engine
# Or use the repo-root docker-compose.yml (recommended; see docs/DEPLOYMENT.md).

# ---------------------------------------------------------------------------
# Stage 1: build the virtualenv (compilers stay out of the final image)
# ---------------------------------------------------------------------------
FROM python:3.12-slim AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_ROOT_USER_ACTION=ignore

# Some transitive deps (litellm/tokenizers) may need a compiler on slim.
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

WORKDIR /build
COPY requirements.txt .
RUN pip install --upgrade pip && pip install -r requirements.txt

# ---------------------------------------------------------------------------
# Stage 2: runtime — no compilers, non-root, application code only
# ---------------------------------------------------------------------------
FROM python:3.12-slim AS runtime

LABEL org.opencontainers.image.title="scaffold-engine" \
      org.opencontainers.image.description="Scaffold engine — shared project awareness for AI coding sessions" \
      org.opencontainers.image.source="https://github.com/themdmohsin/Scaffold"

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

RUN groupadd --system --gid 1001 scaffold \
    && useradd --system --uid 1001 --gid scaffold --create-home scaffold

COPY --from=builder /opt/venv /opt/venv

WORKDIR /app
# app/ contains everything the engine serves (routes, services, db SQL +
# migrations, scripts). tests/ and .env stay out (see .dockerignore).
COPY --chown=scaffold:scaffold app ./app

USER scaffold

EXPOSE 8000

# Liveness (never touches the DB). Orchestrators should use GET /ready for
# readiness — it additionally checks the database and migration manifest.
HEALTHCHECK --interval=30s --timeout=5s --start-period=25s --retries=3 \
    CMD python -c "import sys,urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4).status == 200 else 1)"

# --no-access-log: the engine emits its own structured access lines with
# request ids (app/middleware.py). Migrations auto-apply on startup (idempotent,
# never blocks boot) — inspect with `python -m app.scripts.migrate --status`.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log", "--no-server-header"]
