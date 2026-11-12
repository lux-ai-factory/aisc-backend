# AGENTS.md — aisc-backend

The **backend layer**: one Django app graph, the HTTP API (`/api/v1/`), the domain models and the
only migration tool. The webapp and the eval worker reach it over HTTP.

## Read first

The binding rules live in the parent repository, `lux-ai-factory/aisc`:
`ARCHITECTURE.md` (invariants I1–I11), `docs/refactor-plan.md` (the phased plan) and
`tests/architecture/` (the guards that enforce them).

## What binds this repo

- **I3** — one implementation per concern: extend the existing model/router/service, never add a
  parallel one.
- **I4** — flags, not modes: `INSTALLED_APPS`, `MIDDLEWARE` and the URLconf never change with
  configuration.
- **I5 / D11** — the domain lives in the model layer: one migration tool (Django's), and an installed
  app owns its tables.
- **I9** — this repo is a *layer*, not a business domain; a domain is a feature inside it.
- **I7** — no secret leaves its point of use; scrub anything outbound.

## Build and test

```bash
uv sync
uv run manage.py check
uv run pytest aisc_backend/tests      # or: uv run manage.py test
uv run ruff check .
```

## Do not

- branch the app graph on configuration (I4);
- add a second migration tool, or hand-write SQL against another app's schema (I5);
- reach into a domain repo or a second backend (I3, I9).
