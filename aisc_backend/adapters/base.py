"""
The adapter contract (CON-01): what a protocol needs to turn a test prompt into a
call and a response into an answer.

An adapter is a small, pure pair of functions — `build_request` and
`parse_response` — over one `Connection` and its resolved `Secret`. Nothing here
touches the database or the network, so each protocol is testable on its own; the
`try_connection` driver in `adapters/__init__.py` is the only part that calls out.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Protocol

#: A `{{name}}` placeholder in a REST body or path.
_PLACEHOLDER = re.compile(r"\{\{\s*([a-zA-Z0-9_.]+)\s*\}\}")
#: A `data.items[0].text` style JSON path.
_PATH_TOKEN = re.compile(r"([^.\[\]]+)|\[(\d+)\]")


@dataclass(frozen=True)
class Secret:
    """A connection's key and secret headers, resolved for one call."""

    key: str | None = None
    headers: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class Request:
    method: str
    url: str
    headers: dict[str, str] = field(default_factory=dict)
    json_body: Any = None
    text_body: str | None = None


@dataclass(frozen=True)
class Answer:
    ok: bool
    answer: Any = None
    refused: bool = False
    refusal_reason: str | None = None
    error: str | None = None
    detail: str = ""
    capabilities: dict[str, bool] = field(default_factory=dict)


class Adapter(Protocol):
    kind: str
    label: str

    def build_request(
        self, connection, secret: Secret, *, input: Any, history: list | None = None,
        params: dict | None = None,
    ) -> Request: ...

    def parse_response(self, status: int, text: str, connection) -> Answer: ...


def context_of(input: Any, history: list | None, params: dict | None) -> dict[str, Any]:
    return {"input": input, "history": history or [], "params": params or {}}


def _lookup(name: str, context: dict[str, Any]) -> Any:
    value: Any = context
    for part in name.split("."):
        if isinstance(value, dict) and part in value:
            value = value[part]
        else:
            return f"{{{{{name}}}}}"
    return value


def render(template: Any, context: dict[str, Any]) -> Any:
    """Substitute `{{input}}`, `{{history}}` and `{{params.x}}` through a JSON body.

    A value that is exactly one placeholder is replaced by the value itself (so
    `{"applicant": "{{params.applicant}}"}` can carry an object); a placeholder
    inside a longer string is interpolated as text.
    """
    if isinstance(template, str):
        stripped = template.strip()
        match = _PLACEHOLDER.fullmatch(stripped)
        if match:
            return _lookup(match.group(1), context)
        return _PLACEHOLDER.sub(lambda m: str(_lookup(m.group(1), context)), template)
    if isinstance(template, dict):
        return {k: render(v, context) for k, v in template.items()}
    if isinstance(template, list):
        return [render(v, context) for v in template]
    return template


def json_path(document: Any, path: str) -> Any:
    """Read `data.items[0].text` out of a decoded JSON document."""
    value = document
    for name, index in _PATH_TOKEN.findall(path or ""):
        if index:
            if not isinstance(value, list) or int(index) >= len(value):
                return None
            value = value[int(index)]
        elif isinstance(value, dict):
            value = value.get(name)
        else:
            return None
    return value


def parse_json(text: str) -> Any:
    try:
        return json.loads(text)
    except (ValueError, TypeError):
        return None


def secret_headers(connection, secret: Secret) -> dict[str, str]:
    """The Authorization/key header plus any stored secret header.

    `secret_header` is either a literal header line (`X-Token: {{secret}}`) or
    empty, which means the protocol's own default. `{{secret}}` is replaced by
    the key.
    """
    headers: dict[str, str] = {}
    line = (connection.secret_header or "").strip()
    if line:
        name, _, value = line.partition(":")
        value = value.replace("{{secret}}", secret.key or "")
        headers[name.strip()] = value.strip()
    elif secret.key:
        headers["Authorization"] = f"Bearer {secret.key}"
    headers.update(secret.headers)
    return headers
