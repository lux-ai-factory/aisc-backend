# ---- Base ----
FROM ghcr.io/astral-sh/uv:python3.12-alpine AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy

WORKDIR /app

# ---- Builder ----
FROM base AS builder

ENV UV_HTTP_TIMEOUT=300

RUN apk add --no-cache git github-cli

RUN --mount=type=secret,id=git_pat \
    if [ ! -s /run/secrets/git_pat ]; then echo "Error: 'GIT_PAT' terminal variable not set"; exit 1; fi && \
    cat /run/secrets/git_pat | tr -d '\n\r' | gh auth login --with-token && \
    gh auth setup-git

# Install dependencies first (caching layer)
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --no-dev

COPY . .

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --upgrade-package a4s-plugin-manager \
        --no-dev

# ---- Final runtime image ----
FROM base AS runtime

ENV UV_NO_SYNC=1

WORKDIR /app

# 1. Copy the code first
COPY . .

# Copy installed virtualenv from builder
COPY --from=builder /app/.venv /app/.venv

ENV VIRTUAL_ENV=/app/.venv \
    PATH="/app/.venv/bin:$PATH"

# Collect static files (for Django admin)
RUN uv run manage.py collectstatic --noinput

EXPOSE 8000

# Default command:
# - Run migrations before starting ASGI server
CMD uv run manage.py migrate --noinput && \
    uv run uvicorn config.asgi:application --host 0.0.0.0 --port 8000 --no-access-log
