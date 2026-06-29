# Audit events — what we log & where it lands

All audit events from every app are stored immutably (tamper-proof) in **immudb**, in the **`audit_log`** table.

## The `audit_log` table (immudb SQL)
| column | meaning |
|---|---|
| `id` | auto-increment |
| `who` | the acting user (from the verified Keycloak token; `eval-worker`/`unknown` for system calls) |
| `what` | the action, `object:verb` (e.g. `evaluation:run`) |
| `app` | where it came from: `backend` · `webapp` · `controls` · `qualification` |
| `occurred_at` | timestamp (UTC) |
| `consequence` | JSON: what actually changed |
| `status` | `ok` / `failed` |
| `summary` | human-readable one-liner of the event |

How it gets there: the **backend is the single writer**. The webapp's actions are logged by the backend
directly; **controls** and **qualification** POST their events to `POST /api/v1/audit` (forwarding the
user's token — the server sets `what`, so it can't be forged). Reading/verifying the log is admin-only
(`GET /api/v1/audit`, requires the `admin` role).

## Events

### webapp → backend
| what | when |
|---|---|
| `project:create` / `project:update` | create / rename a project |
| `dataset:create` / `dataset:upload` | add a dataset / upload its file |
| `model:create` / `model:upload` | register a model / upload its file |
| `plugin:install` / `plugin:disable` / `plugin:toggle` / `plugin:config` | manage project plugins |
| `evaluation:run` | start an evaluation |

### system (eval-worker) → backend — evaluation lifecycle
| what | when |
|---|---|
| `evaluation:status` | evaluation status changes |
| `evaluation:plugin_failed` | a plugin run failed |
| `evaluation:artifact` | an artifact was produced |
| `evaluation:measures` | measurements recorded |

### controls
| what | when |
|---|---|
| `checklist:answer` | submit a filled checklist |
| `checklist:review` | save a reviewed checklist |
| `source:create` | add a source/authority |
| `submission:save_draft` / `submission:close` | save / close a submission draft |
| `submission:reopen` / `:archive` / `:restore` | (planned — triggered from a server component; needs token-forwarding before they can attribute "who") |

### qualification
| what | when |
|---|---|
| `qualification:create` | qualify an AI system |
| `systemcard:generate` | generate its system card |

> `what` is always server-decided (never from the request body); `who` comes from the verified token.
