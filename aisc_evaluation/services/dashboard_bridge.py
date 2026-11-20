"""Tell the dashboard which projects exist (ANA-01, the pilot's `dashboard_bridge`).

Superset keeps one set of objects per project — a connection to its database,
datasets, a role and a dashboard — and its own bridge API makes and removes them.
The platform calls that bridge, with a shared token, `X-AISC-Bridge-Token`:

    POST   /api/v1/aisc_project/<pid>            {"slug", "name"}   register
    DELETE /api/v1/aisc_project/<pid>                              unregister
    PUT    /api/v1/aisc_project/<pid>/plugins    {"plugin", ...}    one tool's tile

Two rules are ported exactly:

* **No token, no call.** The dashboard refuses every call whose token is empty,
  and so do we: an unauthenticated call into a process that holds every
  project's read credentials is worse than a missing tile. `BridgeClosed` says
  so, and no request leaves the process.
* **Best-effort by design.** A dashboard that is down, slow or not configured
  never stops a person from working: every call returns False (or raises
  `BridgeUnavailable` for a tile someone asked for and must be told about)
  and logs, rather than failing the request that caused it.
"""
from __future__ import annotations

import logging

from django.conf import settings

logger = logging.getLogger(__name__)

#: Registration is a couple of objects; a tile sync imports through Superset.
TIMEOUT_SECONDS = 5
TILE_TIMEOUT_SECONDS = 60
#: The header the dashboard's bridge reads its shared token from.
TOKEN_HEADER = "X-AISC-Bridge-Token"


class BridgeClosed(RuntimeError):
    """No bridge is configured, or no token — nothing is called."""


class BridgeUnavailable(RuntimeError):
    """The dashboard did not take what was asked of it."""


def _base() -> str:
    return (getattr(settings, "RESULTS_DASHBOARD_BRIDGE_URL", "") or "").strip().rstrip("/")


def _token() -> str:
    return (getattr(settings, "RESULTS_DASHBOARD_BRIDGE_TOKEN", "") or "").strip()


def configured() -> bool:
    """Whether a call could be made at all (a URL *and* a token)."""
    return bool(_base() and _token())


def _request(method: str, path: str, body: dict | None, timeout: float):
    """One call, or `BridgeClosed`. Imported here so the module loads without httpx."""
    import httpx

    base, token = _base(), _token()
    if not base:
        raise BridgeClosed("no dashboard bridge is configured (RESULTS_DASHBOARD_BRIDGE_URL)")
    if not token:
        raise BridgeClosed(
            "no dashboard bridge token is configured (RESULTS_DASHBOARD_BRIDGE_TOKEN): "
            "the dashboard refuses every call without one, so none is made"
        )
    headers = {"Content-Type": "application/json", TOKEN_HEADER: token}
    with httpx.Client(timeout=timeout) as client:
        response = client.request(method, f"{base}{path}", json=body, headers=headers)
    if response.status_code >= 400:
        raise BridgeUnavailable(f"the dashboard answered {response.status_code}")
    if not response.content:
        return {}
    try:
        return response.json()
    except ValueError:
        return {}


def register(project) -> bool:
    """The project exists: make its dashboard objects. False when it was not taken."""
    try:
        _request("POST", f"/api/v1/aisc_project/{project.pid}",
                 {"slug": _slug(project), "name": project.name}, TIMEOUT_SECONDS)
        return True
    except BridgeClosed:
        logger.info("the dashboard bridge is closed; project %s not registered", project.pid)
        return False
    except Exception:  # noqa: BLE001 - best effort, by design
        logger.warning("the dashboard did not take project %s", project.pid, exc_info=True)
        return False


def unregister(project) -> bool:
    """The project is going: remove its dashboard objects."""
    try:
        _request("DELETE", f"/api/v1/aisc_project/{project.pid}", None, TIMEOUT_SECONDS)
        return True
    except BridgeClosed:
        return False
    except Exception:  # noqa: BLE001
        logger.warning("the dashboard did not drop project %s", project.pid, exc_info=True)
        return False


def sync_tool(
    project,
    tool: str,
    label: str,
    version: str = "",
    project_name: str = "",
    visualizations: list[dict] | None = None,
    again: bool = False,
) -> dict:
    """Make or update one tool's tile from the default charts its plugin declares.

    The body is the dashboard's own bridge contract (`aisc_ext.plugin_tiles.plugin_request`):
    `plugin` is the engine's `<package>::<class>`, `visualizations` the plugin's
    default charts as the engine serves them (`metric_visualizations`), `version`
    and `project_name` label the tile, and `again` remakes a tile someone deleted
    (a tile is only made again when that is asked). `BridgeUnavailable` when a configured dashboard did not
    take it, so the person who asked is told; `BridgeClosed` when there is nothing
    to call.
    """
    body = {
        "plugin": tool,
        "label": label,
        "version": version,
        "project_name": project_name or (project.name or ""),
        "visualizations": visualizations or [],
        "again": again,
    }
    try:
        answer = _request("PUT", f"/api/v1/aisc_project/{project.pid}/plugins", body,
                          TILE_TIMEOUT_SECONDS)
    except BridgeUnavailable:
        raise
    except BridgeClosed:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.warning("the dashboard did not take tool %s of project %s", tool, project.pid,
                       exc_info=True)
        raise BridgeUnavailable(f"the dashboard did not take the tile for {tool}") from exc
    return {
        "plugin": tool,
        "slug": answer.get("slug", tool),
        "charts": int(answer.get("charts", 0) or 0),
        "deleted": answer.get("deleted") is True,
    }


def _slug(project) -> str:
    """The dashboard's own handle for the project: stable, and safe in a URL."""
    import re

    slug = re.sub(r"[^a-z0-9]+", "-", (project.name or "").lower()).strip("-")
    return slug or str(project.pid)
