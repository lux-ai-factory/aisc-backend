# AISC Backend

This repository is the API of **the engine** of AISC (the AI Assessment Sandbox Configurator): a Django service
with a Django Ninja API under `/api/`. The engine installs the tests chosen in step 3 (Choose tests and
checklists) and runs them in step 4 (Collect evidence). In the AISC stack this repository runs as the compose
services `aisc-backend`, `aisc-backend-migrate` and `aisc-backend-ledger`.

## Stack architecture (high-level)

The engine is made of three repositories. Inside AISC they are git submodules of the
[aisc](https://github.com/lux-ai-factory/aisc) repository:

- **`apps/backend`** (this repo): the API, test discovery and the data behind the test forms; it records runs
  and keeps their results
- **`apps/eval`**: the Celery worker that installs and runs the tests (`aisc-eval-worker`)
- **`apps/webapp`**: the engine's pages (`aisc-webapp`)

When someone runs a test, `aisc-backend` records the run, stamps it with the latest AI card version and queues it
(Celery, through `rabbitmq` and `redis`). The worker runs it and posts the measurements and the files the test
writes back to this service's internal API (`/api/v1/internal`), which keeps the measurements in the engine's
schema of the project's database and the files in MinIO (bucket `artifacts`). For the whole picture, see
`docs/architecture.md` ("Running tests") and `docs/appendices/4-collect-evidence.md` in the aisc repository.

The Docker Compose files that run the stack live at the root of the `aisc` repository, not here.

### Plugin system (how it works)

Each test is a plugin: a Python package written against the plugin interface (`shared/plugin-interface` in the
aisc repository) and found through the plugin manager (`shared/plugin-manager`).

- **`aisc-backend`** loads a plugin to **list it and build its configuration and run forms** (its settings, its
  inputs, what it assesses and the helper models it needs). It never runs the plugin's evaluation.
- **`aisc-eval-worker`** installs the plugin in a fresh environment and **runs it**.
- In the Docker stacks both containers mount the folder named by `PLUGIN_PATH` at `/app/plugins`, so the
  plugins in `local_plugins/` of the aisc repository are found there.

---

## Two deployment modes

One environment variable, `AISC_DEPLOYMENT`, read once in `aisc_backend/deployment.py`, decides
how the backend runs:

| Mode | When | What changes |
|---|---|---|
| `standalone` (default, variable unset) | the engine on its own | one database for all projects; projects are created in the engine; Django admin and allauth login at `/admin/` and `/_allauth/` |
| `configurator` | inside AISC | one PostgreSQL database per platform project; every `/api/*` call names its project in the `X-AISC-Project` header; sign-in is done by the AISC gateway, so the admin, auth, sessions and allauth apps are not loaded |

Any other value stops the backend at start. `configurator` requires:

- `DB_ENGINE=django.db.backends.postgresql` (only the test runner may use sqlite);
- `RUN_TICKET_KEY`, the key of the run tickets the eval worker carries. It must differ from
  `DJANGO_SECRET_KEY` and is given to the backend only: the worker holds `DJANGO_SECRET_KEY` and runs plugin
  code;
- `AUTH_ENABLED=true` (with it off, the door checks no caller's membership or role).

A database records the mode it was made in (migration 0021), and the backend refuses to migrate or serve a
database made by the other mode.

---

## Choose your workflow

### A) Inside the AISC stack (the usual way)

From the root of the `aisc` repository, run `uv run scripts/aisc.py start` (see that README). It makes the
secrets if they are missing and starts the stack. This repo is built into three services of
`docker-compose.development.yml`, all with `AISC_DEPLOYMENT: configurator`:

- **`aisc-backend-migrate`**: a one-shot that runs `manage.py migrate_projects`, which applies the migrations in
  every project database (schema `engine`) and grants the report and dashboard readers their access. It exits
  when done; exit code 2 is a permanent error.
- **`aisc-backend`**: the API, started with
  `uvicorn config.asgi:application --host 0.0.0.0 --port 8000` once the one-shot has completed. Browsers reach it
  through the gateway (Caddy and oauth2-proxy) at `http://localhost/api/`; the other services call it directly
  at `http://aisc-backend:8000`.
- **`aisc-backend-ledger`**: runs `manage.py deliver_ledger_events --every 10`, which sends the engine's
  activity events from each project database's outbox to the platform, for the ledger. It is the only engine
  process that holds the engine's ledger token (`PLATFORM_LEDGER_ENGINE_TOKEN`), and it never imports plugin
  code.

On start each of them installs this checkout's `shared/plugin-manager`, `shared/plugin-interface` and
`shared/identity` in editable mode, so a change there reaches them on a restart.

Never run a plain `manage.py migrate` against the stack: in configurator mode the migrations run
per project database, through `migrate_projects`.

To run the engine on its own, use the `aisc` repo's `docker-compose.engine-standalone.yml` (see "The engine on
its own" in `docs/guide.md` of the aisc repository). There `AISC_DEPLOYMENT` is unset, and this repo's
`Dockerfile` runs `manage.py migrate` before it serves on port 8000.

### B) Local development (developing **this backend**)

Use this when you change Django code in this repo and run it on the host. Start only the infrastructure it
needs (PostgreSQL, Redis, RabbitMQ, MinIO and immudb):

1. Set `AISC_ADMIN_PORTS=on`, in the environment or in `env.local` in the aisc folder. The infrastructure
   ports (5432, 6379, 5672, 9000 and 3322) are then published, on `127.0.0.1` only.
2. In the aisc folder, run `uv run scripts/aisc.py secrets`. It prints this install's exact compose
   command, which ends in `up -d`.
3. Append the service names to that command: `postgres redis rabbitmq minio immudb`.

`uv run scripts/aisc.py start` instead brings up everything, the backend containers included. Use it to
run this module inside the full stack.

#### Prerequisites
- Python 3.12+
- Git
- uv

#### Setup

```
cd apps/backend
uv sync
```

The lock takes `aisc-plugin-interface` 0.4.0, `aisc-plugin-manager` 0.3.0+aisc.1 and `aisc-identity` 0.1.0
from the wheels under `vendor/`. They are built from the aisc checkout by the root's
`scripts/vendor-plugin-interface.sh`, which refreshes them when one of those libraries changes. To work on the
interface or the plugin manager themselves, install them from the aisc repo in editable mode:

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

From the root of the aisc repository, the way CI runs them:

```
scripts/lib/engine-tests.sh              # sqlite, standalone mode; leaves out tests/integration
scripts/lib/engine-tests.sh --postgres   # the project database tests, configurator mode, throwaway Postgres
```

The first runs every test package under `aisc_backend/tests` except `integration` (upstream's, which needs a
running Keycloak and immudb and skips without them) on a fresh sqlite file. The second runs the tests that need
a PostgreSQL superuser on a throwaway container and fails if any of them skipped. From this folder,
`uv run python manage.py test` runs the whole suite on sqlite; tests marked configurator-only skip unless
`AISC_DEPLOYMENT=configurator` is set, and the PostgreSQL tests (for example
`aisc_backend/tests/test_isolation_engine_db.py`) skip unless `ENGINE_TEST_SUPERUSER_URL` points at a throwaway
PostgreSQL (the docstring of `aisc_backend/tests/isolation_support.py` lists the setup). Never point tests at
the database of a running stack (port 5432).

#### Useful URLs (standalone)

- Admin: http://127.0.0.1:8000/admin
- OpenAPI docs:
  - allauth endpoints: http://127.0.0.1:8000/_allauth/openapi.html
  - Django Ninja endpoints: http://127.0.0.1:8000/api/docs

---

## Settings

Read in `config/settings.py` (and, for the platform calls, where named). The stack values are those of
`docker-compose.development.yml` and `env.runtime` in the aisc repository; secrets are made by
`uv run scripts/aisc.py secrets` and never written here.

| Variable | Meaning | Default | In the AISC stack |
|---|---|---|---|
| `AISC_DEPLOYMENT` | `standalone` or `configurator` | `standalone` | `configurator` |
| `DB_ENGINE`, `DB_NAME`, `DB_USER`, `DB_PASSWORD`, `DB_HOST`, `DB_PORT` | the database; in configurator mode `DB_NAME` is the membership database and the template of the per-project connections | sqlite file `db.db` | PostgreSQL, `platform`, `engine_rw` |
| `DB_SCHEMA` | schema of the engine's tables in each project database | empty | `engine` |
| `DJANGO_SECRET_KEY` | Django's key; also decrypts stored plugin settings, so the worker holds it too | random per start | from the secrets |
| `RUN_TICKET_KEY`, `RUN_TICKET_TTL_SECONDS` | key and lifetime of the run tickets the worker's internal calls carry | none (required in configurator mode), 86400 | from the secrets |
| `INTERNAL_API_KEY` | shared secret the worker sends as `X-Internal-Secret` on `/api/v1/internal` | none | from the secrets |
| `AUTH_ENABLED`, `KEYCLOAK_ISSUER`, `KEYCLOAK_JWKS_URL` | verify the caller's token against the Keycloak realm | off | on, realm `aisc` |
| `ENGINE_ACCEPTED_CLIENTS` | Keycloak clients whose tokens are accepted | `aisc-gateway,aisc-webapp` | `aisc-gateway` |
| `S3_URL`, `S3_USER`, `S3_PASSWORD`, `S3_DATASETS_BUCKET`, `S3_MODELS_BUCKET`, `S3_ARTIFACTS_BUCKET` | MinIO, for uploaded datasets and models and the files tests write | empty; buckets `datasets`, `models`, `artifacts` | `http://minio:9000` |
| `CELERY_BROKER_URL`, `REDIS_BACKEND_URL` | the task queue and its result store | empty | `rabbitmq`, `redis` |
| `PLUGIN_PATH` | folder of local plugins | empty | `./local_plugins/`, mounted at `/app/plugins` |
| `PACKAGE_REGISTRY_URL`, `PACKAGE_REGISTRY_INDEX`, `PACKAGE_REGISTRY_USER`, `PACKAGE_REGISTRY_PASSWORD` | the package index tests are installed from | empty | `http://devpi:3141`, `root/public`, no login |
| `IMMUDB_URL`, `IMMUDB_USER`, `IMMUDB_PASSWORD`, `IMMUDB_DATABASE` | the engine's own audit log (`audit/clerk.py`) | `localhost:3322`, `immudb`, empty, `auditdb` | `immudb:3322`, `aisc_engine`, `auditdb` |
| `LEDGER_MODE` | `off`, `record` or `enforce`: whether changes are written to the ledger outbox (`aisc_backend/ledger.py`) | `off` | `record` |
| `PLATFORM_URL`, `PLATFORM_ENGINE_TOKEN` | ask the platform whether a card target has a connection, and list the project's helper models | empty | `http://platform:8000`, from the secrets |
| `PLATFORM_LEDGER_ENGINE_TOKEN` | send the outbox to the platform (`aisc-backend-ledger` only) | empty | from the secrets |
| `MODEL_LISTING_SSL_VERIFY` | verify TLS when listing an endpoint's models | true | from `env.runtime` |
| `BACKEND_CORS_ALLOWED_ORIGINS`, `ENABLE_SSL_PROXY`, `DEBUG`, `LOG_LEVEL` | web settings | see `config/settings.py` | |

---

## Changes on pilot

`pilot` is the branch AISC uses. Compared with `origin/master`, it adds what the configurator
mode needs and leaves standalone behaviour as on master. The engine is not held to `origin/master`: the list
below is what the branch changes.

- **Deployment mode**: `aisc_backend/deployment.py` and the start checks in
  `aisc_backend/apps.py` and `config/settings.py` (see "Two deployment modes" above).
- **Project databases**: `aisc_backend/projectdb.py` (one database alias per platform project)
  and `manage.py migrate_projects`.
- **The door**: `aisc_backend/project_door.py`, a middleware that, before any view runs, checks
  the `X-AISC-Project` header, the caller's membership in that platform project
  (`aisc_backend/auth/membership.py`), and for the worker's internal calls the
  `X-AISC-Evaluation` and `X-AISC-Run` headers, then opens that project's database for the
  request. In configurator mode creating a project here is refused (projects are made on the
  launcher), and installing or removing a test needs the realm role `admin`. The gateway's
  token header `X-Auth-Request-Access-Token` is accepted as the bearer token.
- **Migrations 0015 to 0025**, appended after master's 0014 without renaming any table:
  `plugin.catalogue_slug` (the catalogue entry a test was installed from); `evaluation.system_id` (the AI card
  version an evaluation is stamped with, the latest when it is created, set by
  `aisc_backend/signals/system_stamp.py` and never changed); `project.platform_project_id` (one engine project per
  platform project); the removal of the login tables in configurator mode; the `engine_deployment` marker table
  (0021); one system target per system (0022); the ledger outbox (0023); 0024, which once removed stored LangBiTe
  judge keys and now changes nothing (it stays for history); and `evaluationinput.helper_ref` (0025), so that a run's input names either a component or a helper
  model (`helper:<platform pid>/<name>`).
- **Routes mounted in configurator mode only**:
  - `POST /api/v1/projects/for-platform/{id}`: the engine's project for a platform project, made on first visit;
  - `GET /api/v1/plugins/{pid}/input_definitions`: an override of master's route that arranges the run form so
    that every evaluation names what is assessed: the `target` input (the system or a component of the AI card)
    or the test's local model file, first; then the test's other inputs; then one `helper:<role>` input per
    helper model it declares (`aisc_backend/routers/target_input.py`);
  - `GET /api/v1/plugins/declarations`: what each enabled test declares (what it assesses, its protocols, its
    helper model roles, its settings fields);
  - `GET /api/v1/projects/{pid}/helpers`: the project's helper models, read from the platform by reference, never
    with their keys (`aisc_backend/routers/helper_models.py`).
- **Run checks**: before it stores an evaluation on a card target, the backend asks the platform whether that
  target has a connection (`GET /internal/projects/{pid}/targets/{key}/reachable`, with `PLATFORM_ENGINE_TOKEN`;
  the answer carries no key). When the platform cannot answer, the evaluation is accepted and the worker reports
  the reason if the run cannot reach the system.
- **Evaluation dispatch**: `services/celery_service.py` sends the worker the same
  `run_evaluation(evaluation_pid)` arguments as master; in configurator mode it adds the project,
  evaluation and run ticket in the Celery message headers.
- **Access**: the door and `auth/membership.py` admit callers with the platform's shared rule
  (`aisc_identity.access`), and `auth/keycloak.py` accepts only tokens issued to the clients in
  `ENGINE_ACCEPTED_CLIENTS` (AISC sets the gateway's).
- **Ledger**: every change of a project, component, setting, secret, test and run is written to
  an outbox in the project's database (`aisc_backend/ledger.py`, `signals/ledger.py`) in the change's own
  transaction; `manage.py deliver_ledger_events` sends it to the platform (the `aisc-backend-ledger` service). A
  request id is kept only on a request the gateway sent.
- **Stored LangBiTe judge keys**: `manage.py clean_old_secrets apply` (run by the root's
  `scripts/clean-old-secrets.sh --apply`) removes `judge_api_key` and `judge_model` from stored LangBiTe
  configurations, behind the cleanup's backup and dry run. Migration 0024 once did this on upgrade; it now changes
  nothing and stays in the chain for history. The form that saved them in plain text does not have them any more
  and could not clear them: the judge is a helper model picked in the run form. `manage.py clean_old_secrets
  count|apply` also masks or drops other plugin secrets stored in clear and lists the keys to rotate.
- **Audit log**: `audit/clerk.py` logs in to immudb again when immudb has ended its login.
- **Tests** for all of the above under `aisc_backend/tests/`, including a check that the table
  names stay master's.

---

## Troubleshooting checklist

- Is `PLUGIN_PATH` an **absolute path** and does it exist?
- Does your plugin package export the plugin class correctly (so it can be discovered)?
- If you changed dependencies: did you rebuild the image (`--build`)?
- "AISC_DEPLOYMENT is configurator: DB_ENGINE must be ...": configurator mode needs PostgreSQL.
- "RUN_TICKET_KEY must be set" / "must differ from DJANGO_SECRET_KEY": give the backend (not the worker)
  a key of its own; `uv run scripts/aisc.py secrets` in the aisc repo makes one.
- "AUTH_ENABLED must be true": set `AUTH_ENABLED=true` for the backend (the aisc stack's compose does).
- "this database was made by a ... engine": the database belongs to the other mode; use a
  database made by this mode.

## Contributing

We welcome community contributions. Please read our [CONTRIBUTING.md](CONTRIBUTING.md) for details.

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
