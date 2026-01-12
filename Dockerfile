# ---- Base ----
FROM ghcr.io/astral-sh/uv:python3.12-alpine AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy

WORKDIR /app

# ---- Builder ----
FROM base AS builder

RUN apk add git

RUN --mount=type=secret,id=git_pat \
    export GITHUB_TOKEN=$(cat /run/secrets/git_pat) && \
    git config --global url."https://$GITHUB_TOKEN@github.com/".insteadOf https://github.com/

# Install dependencies first (caching layer)
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --no-dev

# Copy project files
COPY . .

# Install project (no dev dependencies)
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

# ---- Final runtime image ----
FROM base AS runtime

WORKDIR /app

# Copy installed virtualenv from builder
COPY --from=builder /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH"

RUN apk add git

RUN --mount=type=secret,id=git_pat \
    export GITHUB_TOKEN=$(cat /run/secrets/git_pat) && \
    git config --global url."https://$GITHUB_TOKEN@github.com/".insteadOf https://github.com/

# Copy app code only (lighter layer)
COPY . .

# Collect static files (for Django admin)
RUN uv run manage.py collectstatic --noinput

EXPOSE 8000

# Default command:
# - Run migrations before starting ASGI server
CMD uv run manage.py migrate --noinput && \
    uv run uvicorn config.asgi:application --host 0.0.0.0 --port 8000 --no-access-log
