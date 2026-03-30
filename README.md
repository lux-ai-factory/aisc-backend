
# A4S Backend 

This repository contains the **A4S backend** service (Django) and the **Docker Compose orchestration** used to run the full A4S stack.

## Stack architecture (high-level)

A4S is split across **three sibling repositories** that should live at the same directory level:

- **`a4s-backend`** (this repo): API, admin, plugin discovery + configuration UI plumbing
- **`a4s-eval`**: evaluation runtime/engine that actually executes plugin evaluations
- **`a4s-webapp`**: frontend web application

Expected filesystem layout:

```
your-workspace/
├── a4s-backend   ← “main” repo (compose files live here)
├── a4s-eval
└── a4s-webapp
```

### Plugin system (how it works)

A4S uses a **plugin system** for evaluation logic:

- **`a4s-backend`** loads plugins to **discover them and render configuration forms**.
- **`a4s-eval`** loads plugins to **run the actual evaluations**.
- In Docker-based plugin development, both containers mount a **shared volume** that points to your local plugin folder, so you can edit plugin code on your machine and have it picked up inside the running containers.

As a plugin developer, you typically only need to:
1) run the appropriate Docker Compose command(s), and  
2) create a plugin project in your local plugin folder.

---

## Choose your workflow

There are two common ways to work with this repo. Pick the one that matches what you’re doing.

### A) Local development (developing **this backend**)

Use this when you’re changing Django code in `a4s-backend` itself.

#### Prerequisites
- Python 3.12+
- Git
- uv

#### Setup

```
git clone https://github.com/lux-ai-factory/a4s-backend.git
cd a4s-backend
uv sync
```

#### Database & admin user

```
uv run python manage.py migrate
uv run python manage.py createsuperuser
```

#### Static files (if needed for admin UI)

```
uv run python manage.py collectstatic
```

#### Run the server

```
uv run python manage.py runserver
```

#### Tests

```
uv run python manage.py test
```

#### Useful URLs

- Admin: http://127.0.0.1:8000/admin
- OpenAPI docs:
  - allauth endpoints: http://127.0.0.1:8000/_allauth/openapi.html
  - Django Ninja endpoints: http://127.0.0.1:8000/api/docs

---

### B) Plugin development (Docker + mounted plugin folder)

Use this when you want to run the full stack in containers and iterate on a plugin locally.

#### Clone the sibling repos

```
cd your-workspace
git clone https://github.com/lux-ai-factory/a4s-backend.git
git clone https://github.com/lux-ai-factory/a4s-eval.git
git clone https://github.com/lux-ai-factory/a4s-webapp.git
```

This workflow mounts your local plugin workspace into the backend + eval containers so both can load the same plugin code.

### Configure `PLUGIN_PATH`

In `env.development`, set `PLUGIN_PATH` to a directory on your machine that will contain one or more plugin projects:

```
PLUGIN_PATH=/absolute/path/to/your/plugins
```

Then create a plugin project folder inside that path (example):

```
/absolute/path/to/your/plugins/
└── my-plugin-project/
```

Follow the instructions in the  [a4s-plugin-interface](https://github.com/lux-ai-factory/a4s-plugin-interface) repository to scaffold and implement the plugin:


### (Linux only) Add `host.docker.internal` to `/etc/hosts`

The backend contacts Keycloak at `http://host.docker.internal:8180`. On macOS/Windows Docker resolves this automatically, but on Linux it does not exist by default. Without it, the OIDC login will fail with a 500 error.

Check if it's already set:
```
getent hosts host.docker.internal
```

If it returns nothing, add it:
```
echo '127.0.0.1 host.docker.internal' | sudo tee -a /etc/hosts
```

### Start the stack in dev mode

From inside `a4s-backend`:

```
docker compose --env-file env.development \
  -f docker-compose-infra.development.yml \
  -f docker-compose.development.yml up
```

### If you need to force dependency refresh / rebuild

```
docker compose --env-file env.development \
  -f docker-compose-infra.development.yml \
  -f docker-compose.development.yml up --build
```

### After pulling changes or testing the Keycloak auth branch

After pulling from a merge request or switching to the `feat/keycloak-audit` branch, rebuild everything:

```
docker compose --env-file env.development \
  -f docker-compose-infra.development.yml \
  -f docker-compose.development.yml down

docker compose --env-file env.development \
  -f docker-compose-infra.development.yml \
  -f docker-compose.development.yml up -d --build
```

---

## Audit logging (immudb)

The A4S stack includes tamper-proof audit logging via [immudb](https://github.com/codenotary/immudb). The **a4s-eval** worker writes cryptographically verified audit events to immudb during evaluation execution. The **a4s-backend** reads those events and serves them via API with optional cryptographic verification.

### Audit API endpoints

| Endpoint | Description |
|---|---|
| `GET /api/v1/audit/events?evaluation_id=&event_type=&limit=&offset=` | List audit events with filters |
| `GET /api/v1/audit/events/{event_id}/verified` | Get a single event with cryptographic tamper-proof verification |
| `GET /api/v1/audit/evaluations/{evaluation_pid}/events` | All events for a specific evaluation |

The `/verified` endpoint uses immudb's `verifiableSQLGet()` to cryptographically prove that the audit record has not been tampered with since it was written. The response includes a `verified: true/false` field.

### Configuration

Set the following environment variables (or add to `env.development`):

```
IMMUDB_HOST=immudb
IMMUDB_PORT=3322
IMMUDB_USER=immudb
IMMUDB_PASSWORD=immudb
IMMUDB_DATABASE=defaultdb
```

For the full technical reference, see [`a4s-eval/docs/audit-logging.md`](../a4s-eval/docs/audit-logging.md).

---

## Troubleshooting checklist

- Are `a4s-backend`, `a4s-eval`, and `a4s-webapp` cloned as **siblings**?
- Is `PLUGIN_PATH` an **absolute path** and does it exist?
- Does your plugin package export the plugin class correctly (so it can be discovered)?
- If you changed dependencies: did you run with `--build`?