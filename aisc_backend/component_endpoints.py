"""Where an LLM component's key may go.

An LLM component names a project secret (`secret_key`) next to the address it is sent to (`endpoint_url`). The
engine's model listing sends that key, so it goes out the way the platform sends its own stored keys: only to an
address the project's network rule allows (the platform's rule for a project linked to one, else the engine's
CONNECTIONS_ALLOWED_HOSTS, as for any direct call of the plugin interface), checked again on the address the socket
reaches, with no redirect followed and no answer body in the error. The guard is the plugin interface's, the one
the platform and the worker use.

A key also belongs to the address it was entered for: `must_type_the_key_again` says when an edit moves a component
to another origin (scheme, host, port) while it keeps the same secret.
"""
from __future__ import annotations

import json
import socket
import ssl
import urllib.error
import urllib.request
from urllib.parse import urlsplit

from aisc_plugin_interface import ModelListingError
from aisc_plugin_interface.connections import transport

from aisc_backend import platform_helpers

#: seconds the endpoint has to answer the listing
LISTING_TIMEOUT = 15
#: the label a refusal or failure starts with
LABEL = "the model listing endpoint"

_DEFAULT_PORTS = {"http": 80, "https": 443}
#: why an edit that moves a component's endpoint to another origin with the same secret is refused
ENTER_THE_KEY_AGAIN = ("the endpoint now points to another host, port or scheme than the one its secret was "
                       "entered for; enter the key again (secret_value)")


def network_rule(project) -> dict:
    """The network rule the component's key goes out under: the platform project's (ModelListingError when it
    cannot be read), or, for a project linked to no platform project, the engine's CONNECTIONS_ALLOWED_HOSTS."""
    if project.platform_project_id is None:
        return {"allowed_hosts": transport.allowed_hosts_from_env(), "denied_addresses": [], "floor_hosts": None}
    try:
        return platform_helpers.network_rule(str(project.platform_project_id))
    except platform_helpers.HelpersUnavailable as exc:
        raise ModelListingError(exc.message) from None


def list_models(base_url: str, api_key: str | None, rule: dict, verify_ssl: bool = True) -> list[str]:
    """The model ids of an OpenAI-compatible `<base_url>/v1/models`, asked under `rule`. ModelListingError with a
    fixed sentence when the rule refuses the address, the endpoint redirects, fails or answers what is not a list:
    never the endpoint's answer body, never the key."""
    url = f"{base_url.rstrip('/')}/v1/models"
    allowed, denied, floor = rule.get("allowed_hosts"), rule.get("denied_addresses"), rule.get("floor_hosts")
    try:
        transport.guard_url(url, allowed, denied, floor)
    except transport.BlockedAddress as refused:
        raise ModelListingError(f"{LABEL} is not allowed by the project's network rule: {refused}") from None
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    opener = transport._OPENER if verify_ssl else urllib.request.build_opener(
        transport._NoRedirect, transport._GuardedHTTPHandler,
        transport._GuardedHTTPSHandler(context=ssl._create_unverified_context()))
    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with transport._Policy(allowed, denied, floor), opener.open(req, timeout=LISTING_TIMEOUT) as resp:
            payload = json.loads(transport._read_capped(resp, LABEL) or b"null")
    except transport._Redirected as exc:
        raise ModelListingError(f"{LABEL} answered with a redirect ({exc.code}), which is not followed") from None
    except urllib.error.HTTPError as exc:
        raise ModelListingError(f"{LABEL} answered HTTP {exc.code}") from None
    except transport.BlockedAddress as refused:
        raise ModelListingError(f"{LABEL} is not allowed by the project's network rule: {refused}") from None
    except transport.EndpointBadResponse as exc:
        raise ModelListingError(str(exc)) from None
    except ValueError:
        raise ModelListingError(f"{LABEL} did not answer JSON") from None
    except (urllib.error.URLError, TimeoutError, socket.timeout, OSError):
        raise ModelListingError(f"could not reach {LABEL}") from None
    except Exception as exc:  # noqa: BLE001 - a dropped connection and the like: its kind only
        raise ModelListingError(f"could not reach {LABEL} ({type(exc).__name__})") from None
    data = payload.get("data") if isinstance(payload, dict) else payload
    if not isinstance(data, list):
        return []
    return [item["id"] for item in data if isinstance(item, dict) and isinstance(item.get("id"), str) and item["id"]]


def origin(url) -> tuple[str, str, int | None] | None:
    """(scheme, host, port) of an address, as a client reaches it: host lower case without a trailing dot, the
    scheme's default port when none is written. None without an address."""
    if not isinstance(url, str) or not url.strip():
        return None
    parts = urlsplit(url.strip())
    scheme = parts.scheme.lower()
    host = (parts.hostname or "").rstrip(".").lower()
    try:
        port = parts.port
    except ValueError:
        port = None
    return scheme, host, port if port is not None else _DEFAULT_PORTS.get(scheme)


def must_type_the_key_again(before: dict | None, after: dict | None) -> bool:
    """Whether an edit of an LLM component's value from `before` to `after` keeps its secret (the same non-empty
    secret_key) while its endpoint moves to another origin: that key was entered for the old address."""
    before, after = before or {}, after or {}
    key = before.get("secret_key")
    if not key or after.get("secret_key") != key:
        return False
    return origin(before.get("endpoint_url")) != origin(after.get("endpoint_url"))
