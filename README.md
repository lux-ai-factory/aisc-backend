
# AISC Backend 

This repository contains the **AISC backend** service (Django) and the **Docker Compose orchestration** used to run the full AISC stack.

## Stack architecture (high-level)

AISC is split across **three sibling repositories** that should live at the same directory level:

- **`aisc-backend`** (this repo): API, admin, plugin discovery + configuration UI plumbing
- **`aisc-eval`**: evaluation runtime/engine that actually executes plugin evaluations
- **`aisc-webapp`**: frontend web application

Expected filesystem layout:

```
your-workspace/
├── aisc-backend   ← “main” repo (compose files live here)
├── aisc-eval
└── aisc-webapp
```

### Plugin system (how it works)

AISC uses a **plugin system** for evaluation logic:

- **`aisc-backend`** loads plugins to **discover them and render configuration forms**.
- **`aisc-eval`** loads plugins to **run the actual evaluations**.
- In Docker-based plugin development, both containers mount a **shared volume** that points to your local plugin folder, so you can edit plugin code on your machine and have it picked up inside the running containers.

As a plugin developer, you typically only need to:
1) run the appropriate Docker Compose command(s), and  
2) create a plugin project in your local plugin folder.

---

## Catalogue one-click install

The backend exposes `POST /api/v1/catalogue/install` so a **Catalogue** UI can
install a plugin `(package_name, version)` from a trusted index and attach it to a
project. It is **off by default** — without the config below the platform behaves
exactly as before, and the endpoint returns 404.

**Flow / guards (in order):** feature flag off → 404 (invisible) · missing/wrong
token → 401 · untrusted index → 403 · non-frozen version → 400 · then the package is
installed from the configured registry and a `Plugin` row is idempotently
get-or-created for the project (a second identical call adds no duplicate). The
operation overrides the global Keycloak auth with a static bearer token (every other
endpoint stays on Keycloak).

### Deployment config (set these env vars where the backend gets its environment)

| Variable | Purpose | Example |
|----------|---------|---------|
| `CATALOGUE_INSTALL_ENABLED` | Enable the endpoint (else 404) | `true` |
| `CATALOGUE_INSTALL_TOKEN` | Static bearer token required from the caller | `<shared secret>` |
| `CATALOGUE_TRUSTED_INDEXES` | Allowlist of trusted "simple" index URLs | `https://<catalogue>/devpi/root/public/+simple/` |
| `BACKEND_CORS_ALLOWED_ORIGINS` | **Add** the Catalogue origin (browser call) | `...,https://<catalogue>` |

> **Trust invariant (security):** `CATALOGUE_TRUSTED_INDEXES` must match the index the
> plugin `Loader` installs from (`PACKAGE_REGISTRY_URL` + `PACKAGE_REGISTRY_INDEX`),
> so the clicked index is the one actually installed. By default the allowlist is
> derived from `PACKAGE_REGISTRY_*`; keep them consistent.
>
> **Token match:** `CATALOGUE_INSTALL_TOKEN` must equal the Catalogue's
> `VITE_PLATFORM_INSTALL_TOKEN`.
>
> **Restart:** these are read at startup — restart the backend (and the eval worker)
> **once** to activate. That is the only restart required.

Existing local `PLUGIN_PATH` plugins and the plugin router are unaffected; this adds
one isolated router and no migration.

## Choose your workflow

There are two common ways to work with this repo. Pick the one that matches what you’re doing.

### A) Local development (developing **this backend**)

Use this when you’re changing Django code in `aisc-backend` itself.

#### Prerequisites
- Python 3.12+
- Git
- uv

#### Setup

```
git clone https://github.com/lux-ai-factory/aisc-backend.git
cd aisc-backend
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
git clone https://github.com/lux-ai-factory/aisc-backend.git
git clone https://github.com/lux-ai-factory/aisc-eval.git
git clone https://github.com/lux-ai-factory/aisc-webapp.git
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

Follow the instructions in the  [aisc-plugin-interface](https://github.com/lux-ai-factory/aisc-plugin-interface) repository to scaffold and implement the plugin:


### Start the stack in dev mode

From inside `aisc-backend`:

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

---

## Troubleshooting checklist

- Are `aisc-backend`, `aisc-eval`, and `aisc-webapp` cloned as **siblings**?
- Is `PLUGIN_PATH` an **absolute path** and does it exist?
- Does your plugin package export the plugin class correctly (so it can be discovered)?
- If you changed dependencies: did you run with `--build`?

##  Contributing

We welcome community contributions! Please read our [CONTRIBUTING.md](CONTRIBUTING.md) for details.

By submitting contributions, you agree to the [CLA](CLA/CLA_VERA.md) and license your work under [Apache 2.0](LICENSE).

---

##  License

This project is licensed under the [Apache License 2.0](LICENSE).  
© 2024–2026 Université du Luxembourg and Luxembourg Institute of Science and Technology.

---

## Acknowledgments

This work is part of ongoing research and development efforts within the Université du Luxembourg’s digital governance and AI compliance initiatives, including the AI Factory Luxembourg initiative.  
