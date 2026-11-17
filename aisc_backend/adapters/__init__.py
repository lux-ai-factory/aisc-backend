"""
The adapter registry (CON-01) and the two things that actually call out: `try_connection`
(the `/test` endpoint — the one call a test would make) and `discover_agent_card` (the
A2A `.well-known` document that names an agent's interface).

Every connection the platform stores has one of these adapters; the kind is not a label
a reader has to interpret, it selects the code that builds the request and reads the
answer.
"""
from __future__ import annotations

import time
from typing import Any

import httpx

from .a2a import A2aAdapter, AGENT_CARD_PATH, agent_card_url
from .base import Adapter, Answer, Request, Secret, context_of, json_path, parse_json, render, secret_headers
from .oip import OipAdapter
from .openai import OpenAiAdapter
from .rest import RestAdapter

__all__ = [
    "ADAPTERS",
    "A2aAdapter",
    "Adapter",
    "Answer",
    "Request",
    "Secret",
    "agent_card_url",
    "describe",
    "discover_agent_card",
    "get",
    "json_path",
    "kinds",
    "try_connection",
]

#: The revision of the built-in adapters. A run records this alongside the kind, so
#: "which adapter reached the system" is answered by the run, not by today's code.
BUILTIN_VERSION = "1"

_ADAPTERS: tuple[Adapter, ...] = (RestAdapter(), OpenAiAdapter(), A2aAdapter(), OipAdapter())
#: kind -> the adapter that serves it.
ADAPTERS: dict[str, Adapter] = {a.kind: a for a in _ADAPTERS}


def kinds() -> list[dict[str, str]]:
    return [{"kind": a.kind, "label": a.label} for a in _ADAPTERS]


def get(kind: str) -> Adapter | None:
    return ADAPTERS.get(kind)


def describe(connection) -> str:
    """The one-line summary a list shows for a connection."""
    if connection.kind == "openai":
        return "OpenAI-compatible, " + (connection.model or "(no model)") + (
            ", with tools" if connection.supports_tools else ""
        )
    if connection.kind == "oip":
        return "Open Inference Protocol, " + (connection.model or "(no model)")
    if connection.kind == "a2a":
        return "A2A agent " + (connection.protocol_version or "1.0") + (
            ", " + connection.path if connection.path else ""
        )
    if connection.kind == "rest":
        return "REST API " + (connection.method or "POST") + " " + (connection.path or "/")
    return connection.kind


async def _send(client: httpx.AsyncClient, request: Request) -> httpx.Response:
    kwargs: dict[str, Any] = {"headers": request.headers}
    if request.json_body is not None:
        kwargs["json"] = request.json_body
    elif request.text_body is not None:
        kwargs["content"] = request.text_body
    return await client.request(request.method, request.url, **kwargs)


async def try_connection(
    connection, secret: Secret, *, input: Any = "ping", history: list | None = None,
    params: dict | None = None, client: httpx.AsyncClient | None = None,
) -> dict:
    """One call through the connection's adapter, as a test would make it.

    Returns the pilot's try shape: the extracted answer or the system's refusal,
    plus the latency; never raises for an HTTP or network failure.
    """
    adapter = get(connection.kind)
    if adapter is None:
        return {
            "ok": False, "error": "unknown_protocol",
            "detail": f"no adapter for {connection.kind!r}", "latency_ms": 0,
        }
    request = adapter.build_request(connection, secret, input=input, history=history, params=params)
    start = time.monotonic()
    timeout = connection.timeout_s or 60
    try:
        if client is not None:
            response = await _send(client, request)
        else:
            async with httpx.AsyncClient(timeout=timeout) as owned:
                response = await _send(owned, request)
    except httpx.HTTPError as exc:
        return {
            "ok": False, "error": "unreachable", "detail": str(exc),
            "latency_ms": round((time.monotonic() - start) * 1000),
        }
    latency_ms = round((time.monotonic() - start) * 1000)
    answer = adapter.parse_response(response.status_code, response.text, connection)
    return {
        "ok": answer.ok,
        "answer": answer.answer,
        "refused": answer.refused,
        "refusal_reason": answer.refusal_reason,
        "error": answer.error,
        "detail": answer.detail,
        "capabilities": answer.capabilities,
        "latency_ms": latency_ms,
    }


async def discover_agent_card(
    connection, *, client: httpx.AsyncClient | None = None,
) -> dict:
    """Read an A2A agent's card (its `.well-known` document)."""
    version = connection.protocol_version or "1.0"
    if version not in AGENT_CARD_PATH:
        raise ValueError(f"unknown A2A protocol version {version!r}")
    url = agent_card_url(connection.base_url, version)
    timeout = connection.timeout_s or 60
    if client is not None:
        response = await client.get(url)
    else:
        async with httpx.AsyncClient(timeout=timeout) as owned:
            response = await owned.get(url)
    response.raise_for_status()
    return response.json()
