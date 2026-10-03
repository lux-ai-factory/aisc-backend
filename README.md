
# AISC Backend

This repository contains the **AISC backend** service (Django, with a Django Ninja API under
`/api/`). It is part of the execution engine of AISC (the AI Assessment Sandbox Configurator),
together with `aisc-eval` (the Celery worker that runs evaluations) and `aisc-webapp` (the
frontend). The engine is step 4 of the Configurator: installing tests, running evaluations
and storing their results.

## Stack architecture (high-level)

The engine is made of three repositories. Inside the Configurator they are git submodules of the
[aisc](https://github.com/lux-ai-factory/aisc) repository:

- **`apps/backend`** (this repo): API, admin, plugin discovery + configuration UI plumbing
- **`apps/eval`**: evaluation runtime that actually executes plugin evaluations
- **`apps/webapp`**: frontend web application

The Docker Compose files that run the stack live at the root of the `aisc` repository, not here.

### Plugin system (how it works)

AISC uses a **plugin system** for evaluation logic:

- **`aisc-backend`** loads plugins to **discover them and render configuration forms**.
- **`aisc-eval`** loads plugins to **run the actual evaluations**.
- In the Docker stacks, both containers mount the folder named by `PLUGIN_PATH` at
  `/app/plugins`, so you can edit plugin code on your machine and have it picked up inside the
  running containers.

---

## Two deployment modes

One environment variable, `AISC_DEPLOYMENT`, read once in `aisc_backend/deployment.py`, decides
how the backend runs:

| Mode | When | What changes |
|---|---|---|
| `standalone` (default, variable unset) | the engine on its own | one database for all projects; projects are created in the engine; Django admin and allauth login at `/admin/` and `/_allauth/` |
| `configurator` | inside the Configurator | one PostgreSQL database per platform project; every `/api/*` call names its project in the `X-AISC-Project` header; sign-in is done by the Configurator's gateway, so the admin, auth, sessions and allauth apps are not loaded |

Any other value stops the backend at start. `configurator` requires
`DB_ENGINE=django.db.backends.postgresql` (only the test runner may use sqlite). A database
records the mode it was made in (migration 0021), and the backend refuses to migrate or serve a
database made by the other mode.

---

## Choose your workflow

### A) Inside the AISC stack (the usual way)

From the root of the `aisc` repository, follow its README: run `./scripts/secrets.sh` once, then
start the stack with Docker Compose (`docker-compose.plugin_downloader.yml`,
`docker-compose-infra.development.yml` and `docker-compose.development.yml`). This repo is built
into two services of `docker-compose.development.yml`, both with `AISC_DEPLOYMENT: configurator`:

- **`aisc-backend-migrate`**: a one-shot that runs `manage.py migrate_projects`, which applies
  the migrations in every project database (schema `engine`) and grants the report and
  dashboard readers their access. It exits when done; exit code 2 is a permanent error.
- **`aisc-backend`**: the API, started with
  `uvicorn config.asgi:application --host 0.0.0.0 --port 8000` once the one-shot has completed.
  It is reached through the gateway (Caddy + oauth2-proxy) at `http://localhost/api/`, and by the
  other services directly at `http://aisc-backend:8000`.

Never run a plain `manage.py migrate` against the stack: in configurator mode the migrations run
per project database, through `migrate_projects`.

To run the engine on its own without the Configurator, use the `aisc` repo's
`docker-compose.engine-standalone.yml` (described in that README). There `AISC_DEPLOYMENT` is
unset, and this repo's `Dockerfile` runs `manage.py migrate` before it serves on port 8000.

### B) Local development (developing **this backend**)

Use this when you're changing Django code in this repo itself. The infrastructure (PostgreSQL,
Redis, RabbitMQ, MinIO) can be started from the `aisc` repo root with
`docker compose --env-file env.development -f docker-compose-infra.development.yml up`.

#### Prerequisites
- Python 3.12+
- Git
- uv

#### Setup

```
cd apps/backend
uv sync
```

To use the local `plugin-manager` and `plugin-interface` from the `aisc` repo:

```
uv pip install --no-deps -e ../../shared/plugin-manager -e ../../shared/plugin-interface
```

Settings are read from the environment and from a `.env` file, if there is one; `env.development`
is an example (set `PLUGIN_PATH` to your own plugin folder).

#### Database & admin user (standalone mode)

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

This runs on sqlite and needs no database server. Tests marked configurator-only skip unless
`AISC_DEPLOYMENT=configurator` is set. The Postgres tests in
`aisc_backend/tests/test_isolation_engine_db.py` skip unless `ENGINE_TEST_SUPERUSER_URL` points at
a throwaway PostgreSQL; the docstring at the top of that file lists the setup. Never point tests at
the database of a running stack (port 5432).

#### Useful URLs (standalone)

- Admin: http://127.0.0.1:8000/admin
- OpenAPI docs:
  - allauth endpoints: http://127.0.0.1:8000/_allauth/openapi.html
  - Django Ninja endpoints: http://127.0.0.1:8000/api/docs

---

## Changes on feat/unified-modules

`feat/unified-modules` is the branch the Configurator uses. Compared with `origin/master`, it adds
only what the configurator mode needs and leaves standalone behaviour as on master. The list of
changed files, each with its reason, is `scripts/guard-frozen-intended.txt` in the `aisc` repo.

- **Deployment mode**: `aisc_backend/deployment.py` and the start checks in
  `aisc_backend/apps.py` and `config/settings.py` (see "Two deployment modes" above).
- **Project databases**: `aisc_backend/projectdb.py` (one database alias per platform project)
  and `manage.py migrate_projects`.
- **The door**: `aisc_backend/project_door.py`, a middleware that, before any view runs, checks
  the `X-AISC-Project` header, the caller's membership in that platform project
  (`aisc_backend/auth/membership.py`), and for the worker's internal calls the
  `X-AISC-Evaluation` and `X-AISC-Run` headers, then opens that project's database for the
  request. In configurator mode creating a project here is refused (projects are made on the
  launcher), and installing or removing a plugin needs the realm role `admin`. The gateway's
  token header `X-Auth-Request-Access-Token` is accepted as the bearer token.
- **Migrations 0015 to 0021**, appended after master's 0014 without renaming any table:
  `plugin.catalogue_slug` (the catalogue entry a plugin was installed from),
  `evaluation.system_id` (the AI system card version an evaluation ran against, set by
  `aisc_backend/signals/system_stamp.py`), `project.platform_project_id` (one engine project per
  platform project), the removal of the login tables in configurator mode, and the
  `engine_deployment` marker table.
- **Routes mounted in configurator mode only**: `POST /api/v1/projects/for-platform/{id}` (the
  engine's project for a platform project, made on first visit) and an override of
  `GET /api/v1/plugins/{pid}/input_definitions` that adds a required `target` input (the system,
  or the AI card component, an evaluation is about).
- **Evaluation dispatch**: `services/celery_service.py` sends the worker the same
  `run_evaluation(evaluation_pid)` arguments as master; in configurator mode it adds the project,
  evaluation and run ticket in the Celery message headers.
- **Tests** for all of the above under `aisc_backend/tests/`, including checks that master's
  routers, `auth/keycloak.py` and table names are unchanged.

---

## Troubleshooting checklist

- Is `PLUGIN_PATH` an **absolute path** and does it exist?
- Does your plugin package export the plugin class correctly (so it can be discovered)?
- If you changed dependencies: did you rebuild the image (`--build`)?
- "AISC_DEPLOYMENT is configurator: DB_ENGINE must be ...": configurator mode needs PostgreSQL.
- "this database was made by a ... engine": the database belongs to the other mode; use a
  database made by this mode.

## Contributing

We welcome community contributions! Please read our [CONTRIBUTING.md](CONTRIBUTING.md) for details.

By submitting contributions, you agree to the contributor license agreement
([individuals](<AISC ICLA (Individuals).txt>), [entities](<AISC CCLA (Entities).txt>)) and license
your work under [Apache 2.0](LICENSE.md).

---

## License

This project is licensed under the [Apache License 2.0](LICENSE.md).
© 2024–2026 Université du Luxembourg and Luxembourg Institute of Science and Technology.

---

## Acknowledgments

This work is part of ongoing research and development efforts within the Université du Luxembourg’s digital governance and AI compliance initiatives, including the AI Factory Luxembourg initiative.
