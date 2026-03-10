
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

### Provide a GitHub token (for private git deps)

Create a GitHub Personal Access Token with `repo` and `read.org` scope and export it in your shell:

```
export GIT_PAT=<your_pat_here>
```

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

---

## Keycloak OIDC Authentication

A4S uses **Keycloak** as identity provider via the **OpenID Connect (OIDC)** protocol, integrated through **django-allauth**.

### How it works

```
 Browser                 Webapp (nginx)          Backend (Django)           Keycloak
   |                         |                         |                       |
   | 1. Click "Sign in       |                         |                       |
   |    with Keycloak"       |                         |                       |
   |------------------------>| 2. POST /_allauth/      |                       |
   |                         |    .../provider/redirect |                       |
   |                         |------------------------>| 3. Build auth URL      |
   |                         |                         |    (with client_id,    |
   |                         |                         |     redirect_uri,      |
   |                         |                         |     scope=openid)      |
   |<---------------------------------------------------------- 302 to Keycloak |
   |                         |                         |                       |
   | 4. User logs in on Keycloak login page            |                       |
   |-------------------------------------------------------------->            |
   |                         |                         |                       |
   | 5. Keycloak redirects back with an authorization code                     |
   |<-------------------------------------------------------------------------|
   | GET /accounts/oidc/keycloak/login/callback/?code=xxx                      |
   |                         |                         |                       |
   |------------------------>| 6. Proxy to backend     |                       |
   |                         |------------------------>| 7. Exchange code       |
   |                         |                         |    for token  -------->|
   |                         |                         |                       |
   |                         |                         | 8. Receive token,      |
   |                         |                         |    fetch user info,    |
   |                         |                         |    create/link account |
   |                         |                         |    create session+JWT  |
   |<---------------------------------------------------------- 302 to webapp  |
   |                         |                         |                       |
   | 9. User is logged in!   |                         |                       |
```

**Key points:**
- The **webapp (nginx)** proxies `/_allauth/*` and `/accounts/*` requests to the **backend**
- The **backend** handles all OIDC logic via django-allauth (token exchange, user creation)
- **Keycloak** is the external identity provider — users authenticate there, then get redirected back
- The backend exchanges the authorization code for a token **server-to-server** (step 7) — this is why the backend must be able to reach Keycloak over the network

### Configuration

OIDC settings are controlled via environment variables in `env.development`:

| Variable | Description |
|----------|-------------|
| `OIDC_ENABLED` | Enable/disable Keycloak login (`true`/`false`) |
| `OIDC_PROVIDER_ID` | Provider identifier (default: `keycloak`) |
| `OIDC_PROVIDER_NAME` | Display name on the login page |
| `OIDC_SERVER_URL` | Base URL of the Keycloak realm (e.g. `http://host.docker.internal:8180/realms/a4s`) |
| `OIDC_CLIENT_ID` | Client ID registered in Keycloak |
| `OIDC_CLIENT_SECRET` | Client secret for the backend |

The Keycloak realm is pre-configured via `keycloak/realm-export.json` (imported on startup).

### Docker networking caveat

In Docker, each container has its own `localhost`. When Keycloak is configured with `KC_HOSTNAME=http://localhost:8180`, it returns `localhost:8180` URLs in its OIDC discovery document. The **browser** can reach `localhost:8180` (mapped to the Keycloak container), but the **backend container** cannot — `localhost` inside the backend = the backend itself, not Keycloak.

**Solution:** We use `host.docker.internal` as Keycloak's hostname, which resolves to the Docker host from within containers. This requires a one-time `/etc/hosts` entry on the host machine (see prerequisites below).

### Keycloak demo users

| User | Password | Notes |
|------|----------|-------|
| `admin` / `admin` | — | Keycloak admin console (`http://host.docker.internal:8180`) |
| `testuser` | `password` | Pre-configured user in the `a4s` realm |
| `admin@a4s.local` | `admin` | Django superuser (created by the demo script) |

---

## Troubleshooting checklist

- Are `a4s-backend`, `a4s-eval`, and `a4s-webapp` cloned as **siblings**?
- Is `PLUGIN_PATH` an **absolute path** and does it exist?
- Does your plugin package export the plugin class correctly (so it can be discovered)?
- If you changed dependencies: did you run with `--build`?