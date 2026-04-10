# ---- Base ----
FROM ghcr.io/astral-sh/uv:python3.12-alpine AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy\
    VIRTUAL_ENV=/app/.venv \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

# ---- Builder ----
FROM base AS builder

RUN apk add --no-cache git github-cli

# Install dependencies first (caching layer)
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --no-dev

COPY . .

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --upgrade-package vera-plugin-manager \
        --no-dev

# Collect static files (for Django admin)
RUN uv run manage.py collectstatic --noinput

# ---- Final runtime image ----
FROM base AS runtime

ENV UV_NO_SYNC=1

# 1. Copy the code first
COPY . .

# Copy installed virtualenv from builder
COPY --from=builder /app/.venv /app/.venv
COPY --from=builder /app/staticfiles /app/staticfiles

EXPOSE 8000

# Default command:
# - Run migrations before starting ASGI server
CMD uv run manage.py migrate --noinput && \
    uv run uvicorn config.asgi:application --host 0.0.0.0 --port 8000 --no-access-log
