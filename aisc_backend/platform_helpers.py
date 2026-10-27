"""The project's helper models, as the platform lists them (configurator only).

A helper model (a judge, an attacker...) is a model a test uses as a tool, never what it assesses, so
in the Configurator it is not a component of the engine's AI system: the platform keeps it, under
Manage > Helper models, with its endpoint and key. The engine only ever handles its reference
(`helper:<platform pid>/<name>`), its label, its roles and its endpoint's host, and asks for them on
the platform's internal list route with PLATFORM_ENGINE_TOKEN, as it asks whether a card target is
reachable (routers/evaluation.py). The answer carries no key and no full endpoint.

Once, start hands the platform the `llm` components older runs used as helpers, keys included
(`create_helper`, for manage.py move_helper_models): the one request in which a key goes from the
engine to the platform, on the internal network. Nothing here logs it or puts it in a message.
"""
from __future__ import annotations

import json
import logging
import os
from urllib import error, request as urlrequest
from urllib.parse import quote, urlencode

logger = logging.getLogger(__name__)

TIMEOUT = 3  # seconds


class HelpersUnavailable(Exception):
    """The platform's list of helper models cannot be read: not configured, or not answering."""

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class HelperRefused(Exception):
    """The platform answered the create route with a refusal other than a name already taken."""


def _get(url: str, headers: dict) -> tuple[int, object]:
    req = urlrequest.Request(url, method="GET", headers=headers)
    try:
        with urlrequest.urlopen(req, timeout=TIMEOUT) as res:
            return res.status, json.loads(res.read() or b"null")
    except error.HTTPError as exc:
        return exc.code, None


def list_helpers(project_pid: str, role: str | None) -> list[dict]:
    """The platform project's helper models, those allowed for `role` when one is given: each
    {"name", "label", "roles", "ref", "endpoint_host"}. A role the platform does not know has none."""
    base = (os.environ.get("PLATFORM_URL") or "").rstrip("/")
    token = os.environ.get("PLATFORM_ENGINE_TOKEN") or ""
    if not base or not token:
        raise HelpersUnavailable("PLATFORM_URL or PLATFORM_ENGINE_TOKEN is not set: helper models cannot be listed")
    url = f"{base}/internal/projects/{quote(str(project_pid), safe='')}/helpers"
    if role is not None:
        url += "?" + urlencode({"role": role})
    try:
        status, body = _get(url, {"X-AISC-Service-Token": token})
    except Exception as exc:  # noqa: BLE001 - the platform is down, or the URL is wrong
        logger.warning("the platform can't be reached (%s): helper models cannot be listed", type(exc).__name__)
        raise HelpersUnavailable("the platform can't be reached: helper models cannot be listed") from exc
    if status == 422 and role is not None:
        return []
    helpers = body.get("helpers") if status == 200 and isinstance(body, dict) else None
    if not isinstance(helpers, list):
        logger.warning("the platform answered %s on the helper models of project %s", status, project_pid)
        raise HelpersUnavailable(f"the platform answered {status}: helper models cannot be listed")
    return helpers


def create_helper(project_pid: str, *, name: str, label: str, endpoint_url: str, roles: list[str],
                  api_key: str | None) -> dict | None:
    """Make a helper model of the platform project from an engine `llm` component: its view and
    reference ({"name", "ref", ...}), or None when a live helper has the name already. Raises
    HelpersUnavailable when the platform does not answer, HelperRefused when it refuses."""
    base = (os.environ.get("PLATFORM_URL") or "").rstrip("/")
    token = os.environ.get("PLATFORM_ENGINE_TOKEN") or ""
    if not base or not token:
        raise HelpersUnavailable("PLATFORM_URL or PLATFORM_ENGINE_TOKEN is not set: helper models cannot be made")
    body = json.dumps({"name": name, "label": label, "endpoint_url": endpoint_url, "roles": roles,
                       "api_key": api_key}).encode()
    req = urlrequest.Request(f"{base}/internal/projects/{quote(str(project_pid), safe='')}/helpers",
                             method="POST", data=body,
                             headers={"X-AISC-Service-Token": token, "Content-Type": "application/json"})
    try:
        with urlrequest.urlopen(req, timeout=TIMEOUT) as res:
            return json.loads(res.read() or b"null")
    except error.HTTPError as exc:
        if exc.code == 409:
            return None
        try:
            detail = (json.loads(exc.read() or b"null") or {}).get("detail")
        except (ValueError, AttributeError):
            detail = None
        raise HelperRefused(f"the platform answered {exc.code}" + (f": {detail}" if detail else "")) from None
    except Exception:  # noqa: BLE001 - the platform is down, or the URL is wrong; the request held the key
        raise HelpersUnavailable(f"the platform did not answer at {base}") from None
