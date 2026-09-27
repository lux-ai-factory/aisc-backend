# ---- Base ----
FROM ghcr.io/astral-sh/uv:python3.12-alpine AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy\
    VIRTUAL_ENV=/app/.venv \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    PATH="/app/.venv/bin:$PATH"

# Two levels down, as in the repository: pyproject.toml takes plugin-interface
# and plugin-manager from ../../shared, and at runtime compose mounts ./shared
# at /app/shared, which is exactly where that points from here.
WORKDIR /app/apps/backend

# ---- Builder ----
FROM base AS builder

RUN apk add --no-cache git github-cli

# Install dependencies first (caching layer)
# The shared packages are not in this build's context; the container installs
# them from the mounted /app/shared when it starts (see the compose command).
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --no-dev \
        --no-install-package aisc-plugin-interface --no-install-package aisc-plugin-manager

COPY . .

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev \
        --no-install-package aisc-plugin-interface --no-install-package aisc-plugin-manager

# Empty since the admin site went (it was the only thing with static files); the
# folder is still made, for the COPY below and whitenoise.
RUN mkdir -p staticfiles && uv run --no-sync manage.py collectstatic --noinput

# ---- Final runtime image ----
FROM base AS runtime

ENV UV_NO_SYNC=1

# 1. Copy the code first
COPY . .

# Copy installed virtualenv from builder
COPY --from=builder /app/.venv /app/.venv
COPY --from=builder /app/apps/backend/staticfiles /app/apps/backend/staticfiles

EXPOSE 8000

# Default command: the ASGI server only. The engine's tables live in each
# project's database (isolation I7.6): the one-shot `aisc-backend-migrate` runs
# `manage.py migrate_projects` at start, and the engine migrates a project
# database the first time it opens it. Nothing is migrated into `platform`.
CMD uv run uvicorn config.asgi:application --host 0.0.0.0 --port 8000 --no-access-log
