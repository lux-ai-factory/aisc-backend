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
| `project` | **WHICH project** | the project the event belongs to (filterable; derived from `metadata.projectPid`) |
| `outcome` | result | `ok` / `failed` |
| `metadata` | **WHY / details** | JSON: what actually changed; any event inside a project carries `projectPid` |
| `summary` | — | human-readable one-liner (deterministic, no LLM) |

How it gets there: the **backend is the single writer**. The webapp's actions are logged by the backend
directly; **controls** and **qualification** POST their events to `POST /api/v1/audit` (forwarding the
user's token — the server sets `actor` + `source_ip`, so they can't be forged). The `project` column is
filled by `log_action`/the audit door FROM `metadata.projectPid` (one place, never recomputed per site),
so a project's whole log is one `WHERE project = …` query away.

Reading the log:
- `GET /api/v1/audit` — **admin-only**, every event + the tamper `verified` proof.
- `GET /api/v1/audit/project/{pid}` (alias `GET /api/v1/projects/{pid}/logs`) — **any member** of that
  project, only its events. Same shape minus `verified`.


## Events  (`action` on `resource_type`)

### webapp → backend
| action · resource_type | when |
|---|---|
| `create`/`update` · project | create / rename a project |
| `create`/`upload` · dataset | add a dataset / upload its file |
| `create`/`upload` · model | register a model / upload its file |
| `install`/`disable`/`toggle`/`configure` · plugin | manage project plugins |
| `run` · evaluation | start an evaluation |
| `member_added`/`member_role_changed`/`member_removed` · project_member | manage who is in a project (owner only) |

### component
| action · resource_type | when |
|---|---|
| `create`/`update`/`upload` · component | create / edit a component / upload its data file |
| `delete` · component | delete a component (audited with `projectPid`) |

### system (eval-worker) → backend — evaluation lifecycle
| action · resource_type | when |
|---|---|
| `status_change` · evaluation | evaluation status changes |
| `plugin_failed` · evaluation | a plugin run failed (outcome=failed) |
| `upload_artifact` · evaluation | an artifact was produced |
| `record_measures` · evaluation | measurements recorded |

### controls (backend-native — the reference lifecycle, no UI)
| action · resource_type | when |
|---|---|
| `create` · submission | open a draft checklist submission |
| `answer` · submission | save answers on a submission |
| `close` · submission | close a submission (Draft → Closed) |
| `reopen` · submission | reopen → next version (the version chain) |
| `generate` · submission_report | build the PDF report payload for a closed submission |

### catalogue (backend-native)
| action · resource_type | when |
|---|---|
| `install` · control | install a seeded checklist template into the project's library (idempotent) |
| `install` · plugin | install a plugin from the catalogue into the engine loader |
| `export` · control | export a control's install-package (compat surface) |

### qualifying the system (backend-native)
| action · resource_type | when |
|---|---|
| `create` · qualification | qualify an AI system (answers + built card + version) |
| `create` · system_version | create the next AI-card version |
| `update` · questionnaire_selection | select the project's active questionnaire |

### reporting (backend-native)
| action · resource_type | when |
|---|---|
| `generate` · report | assemble the report snapshot and render the PDF/HTML |

### connections (backend-native, CON-01)
| action · resource_type | when |
|---|---|
| `update`/`delete` · connection | create/edit or delete a connection |
| `set_target` · connection | link a connection to the component it reaches |
| `bind` · run_binding | bind the adapter/version and helper a run used (snapshotted) |


> `action` + `resource_type` are always server-decided (never from the request body); `actor` + `source_ip`
> come from the verified request.
