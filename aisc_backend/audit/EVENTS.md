# Audit events — what we log & where it lands

All audit events from every app are stored immutably (tamper-proof) in **immudb**, in the **`audit_log`** table.
The schema follows standard audit-log practice: fields are **queryable** (query "all deletes" or
"everything done to project X"), not one opaque string.

## The `audit_log` table (immudb SQL)
| column | the "W" | meaning |
|---|---|---|
| `id` | — | auto-increment |
| `occurred_at` | **WHEN** | UTC timestamp |
| `actor` | **WHO** | acting user (from the verified Keycloak token; `unknown` for system/anonymous) |
| `action` | **WHAT** | the verb: `create` · `update` · `delete` · `run` · `upload` · `answer` · … |
| `resource_type` | **on WHAT** | object type: `project` · `dataset` · `evaluation` · `plugin` · `checklist` · … |
| `resource_id` | which one | the object's id/pid (nullable) |
| `source_app` | **WHERE** | `backend` · `webapp` · `controls` · `qualification` |
| `source_ip` | **WHERE FROM** | client IP (honours `X-Forwarded-For`) |
| `outcome` | result | `ok` / `failed` |
| `metadata` | **WHY / details** | JSON: what actually changed |
| `summary` | — | human-readable one-liner (deterministic, no LLM) |

How it gets there: the **backend is the single writer**. The webapp's actions are logged by the backend
directly; **controls** and **qualification** POST their events to `POST /api/v1/audit` (forwarding the
user's token — the server sets `actor` + `source_ip`, so they can't be forged). Reading/verifying the log
is admin-only (`GET /api/v1/audit`, requires the `admin` role).

## Events  (`action` on `resource_type`)

### webapp → backend
| action · resource_type | when |
|---|---|
| `create`/`update` · project | create / rename a project |
| `create`/`upload` · dataset | add a dataset / upload its file |
| `create`/`upload` · model | register a model / upload its file |
| `install`/`disable`/`toggle`/`configure` · plugin | manage project plugins |
| `run` · evaluation | start an evaluation |

### system (eval-worker) → backend — evaluation lifecycle
| action · resource_type | when |
|---|---|
| `status_change` · evaluation | evaluation status changes |
| `plugin_failed` · evaluation | a plugin run failed (outcome=failed) |
| `upload_artifact` · evaluation | an artifact was produced |
| `record_measures` · evaluation | measurements recorded |

### controls
| action · resource_type | when |
|---|---|
| `answer` · checklist | submit a filled checklist |
| `review` · checklist | save a reviewed checklist |
| `create` · source | add a source/authority |
| `save_draft`/`close` · submission | save / close a submission draft |
| `reopen`/`archive`/`restore` · submission | (planned — server-component-triggered; needs token-forwarding to attribute the actor) |

### qualification
| action · resource_type | when |
|---|---|
| `create` · qualification | qualify an AI system |
| `generate` · systemcard | generate its system card |

> `action` + `resource_type` are always server-decided (never from the request body); `actor` + `source_ip`
> come from the verified request.
