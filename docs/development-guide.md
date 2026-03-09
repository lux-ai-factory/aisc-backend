# Development Guide — Quick Reference

## Prerequisites

- Docker & Docker Compose
- Python 3.12+ & [uv](https://docs.astral.sh/uv/)
- Git
- A GitHub PAT exported as `GIT_PAT` (for private deps)

## Repository layout

All three repos must be cloned as siblings:

```
your-workspace/
├── a4s-backend   ← Docker Compose orchestration lives here
├── a4s-eval
└── a4s-webapp
```

---

## Running the full stack (Docker)

All commands run from inside **`a4s-backend/`**.

### Start everything

```bash
docker compose --env-file env.development \
  -f docker-compose-infra.development.yml \
  -f docker-compose.development.yml up
```

### Rebuild after dependency changes

```bash
docker compose --env-file env.development \
  -f docker-compose-infra.development.yml \
  -f docker-compose.development.yml up --build
```

### Stop everything

```bash
docker compose --env-file env.development \
  -f docker-compose-infra.development.yml \
  -f docker-compose.development.yml down
```

### Stop and remove volumes (full reset)

```bash
docker compose --env-file env.development \
  -f docker-compose-infra.development.yml \
  -f docker-compose.development.yml down -v
```

---

## Services & ports

| Service | Container | Port | Notes |
|---|---|---|---|
| **Caddy** (reverse proxy) | `caddy` | 443, 80 | TLS termination |
| **a4s-backend** (Django API) | `a4s-backend` | 8000 | API + admin |
| **a4s-webapp** (frontend) | `a4s-webapp` | 5173 | Vite dev server |
| **a4s-eval** (Celery worker) | `a4s-eval-worker` | — | No exposed port |
| **Flower** (Celery monitor) | `a4s-eval-flower` | 5555 | Task monitoring UI |
| **PostgreSQL** | `postgres` | 5432 | Main database |
| **Redis** | `redis` | 6379 | Celery result backend |
| **RabbitMQ** | `rabbitmq` | 5672 | Celery message broker |
| **MinIO** (S3-compatible) | `minio` | 9000 | Object storage |
| **immudb** | `immudb` | 3322, 8080 | Immutable audit logs |

---

## Running tests

### a4s-backend (Django)

```bash
cd a4s-backend
uv sync
uv run python manage.py test
```

Run a specific test module:

```bash
uv run python manage.py test a4s_backend.tests.test_audit_service
```

### a4s-eval (pytest)

```bash
cd a4s-eval
uv sync
uv run pytest -v
```

Run a specific test file:

```bash
uv run pytest tests/test_audit.py -v
```

### Linting (both repos)

```bash
uv run ruff check .
```

---

## Local development (without Docker)

### a4s-backend

```bash
cd a4s-backend
uv sync
uv run python manage.py migrate
uv run python manage.py createsuperuser   # first time only
uv run python manage.py runserver
```

Useful URLs:
- Admin: http://127.0.0.1:8000/admin
- API docs: http://127.0.0.1:8000/api/docs

### a4s-eval

The eval worker needs RabbitMQ, Redis, and immudb running (start them with Docker or locally):

```bash
cd a4s-eval
uv sync
uv run celery -A a4s_eval.celery_worker worker --loglevel=info
```

---

## Environment configuration

Copy and edit the env file in `a4s-backend/`:

```bash
cp env.development.example env.development   # if an example exists
```

Key variables:

| Variable | Description |
|---|---|
| `PLUGIN_PATH` | Absolute path to your local plugins directory |
| `GIT_PAT` | GitHub PAT for private dependencies (export in shell) |
| `API_URL` | Internal backend URL (e.g., `http://a4s-backend:8000`) |
| `S3_USER` / `S3_PASSWORD` | MinIO credentials |
| `POSTGRES_USER` / `POSTGRES_PASSWORD` / `POSTGRES_DB` | PostgreSQL credentials |
| `CELERY_BROKER_URL` | RabbitMQ connection string |
| `REDIS_BACKEND_URL` | Redis connection string |
| `IMMUDB_HOST` / `IMMUDB_PORT` | immudb connection (defaults: `immudb`, `3322`) |

---

## Plugin development

1. Set `PLUGIN_PATH` in `env.development` to an absolute path on your machine
2. Create plugin projects inside that folder (see [a4s-plugin-interface](https://github.com/lux-ai-factory/a4s-plugin-interface))
3. Both `a4s-backend` and `a4s-eval-worker` containers mount this path at `/app/plugins`
4. Start the full stack — plugins are discovered automatically

---

## Troubleshooting

- **Repos not siblings?** Docker Compose build contexts reference `../a4s-eval/` and `../a4s-webapp/` — they must be at the same directory level as `a4s-backend/`.
- **`PLUGIN_PATH` errors?** Must be an absolute path that exists on your machine.
- **Stale images?** Run with `--build` to force a rebuild.
- **immudb connection refused?** Check that immudb container is running: `docker ps | grep immudb`.
- **Celery tasks not executing?** Check RabbitMQ and Redis are up. Monitor via Flower at http://localhost:5555.
