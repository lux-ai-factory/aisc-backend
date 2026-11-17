"""Schemas for connections, helpers and run bindings (CON-01)."""
from typing import Any

from ninja import Schema


class AdapterOut(Schema):
    kind: str
    label: str


class TargetOut(Schema):
    key: str
    name: str
    component_type: str


class ConnectionOut(Schema):
    name: str
    label: str
    kind: str
    protocol: str
    base_url: str
    model: str = ""
    supports_tools: bool = False
    supports_logprobs: bool = False
    protocol_version: str = ""
    path: str = ""
    method: str = ""
    body_template: Any = None
    response_path: str = ""
    headers: dict[str, Any] = {}
    secret_header: str = ""
    timeout_s: int = 60
    settings: dict[str, Any] = {}
    target: TargetOut | None = None
    has_secret: bool = False
    secret_headers: list[str] = []
    last_test_at: Any = None
    last_test_ok: bool = False


class ConnectionListOut(Schema):
    adapters: list[AdapterOut]
    connections: list[ConnectionOut]


class ConnectionIn(Schema):
    """The pilot's save body. A field left out keeps its stored value; `secret` of
    `""` clears the stored key, and a `secret_headers` entry of `""` removes that
    header."""

    label: str = ""
    kind: str = "openai"
    base_url: str = ""
    timeout_s: int = 60
    target: str | None = None
    model: str = ""
    supports_tools: bool = False
    supports_logprobs: bool = False
    protocol_version: str = ""
    path: str = ""
    method: str = "POST"
    body_template: Any = None
    response_path: str | None = None
    refusal: dict[str, Any] | None = None
    headers: dict[str, Any] = {}
    secret_header: str | None = None
    settings: dict[str, Any] = {}
    secret: str | None = None
    secret_headers: dict[str, str] | None = None


class ConnectionTargetIn(Schema):
    key: str


class ConnectionTryIn(Schema):
    input: Any = "ping"
    history: list[Any] | None = None
    params: dict[str, Any] | None = None


class ConnectionTryOut(Schema):
    ok: bool
    answer: Any = None
    refused: bool = False
    refusal_reason: str | None = None
    error: str | None = None
    detail: str = ""
    capabilities: dict[str, bool] = {}
    latency_ms: int = 0


class HelperOut(Schema):
    name: str
    label: str
    endpoint_url: str
    roles: list[str] = []
    has_key: bool = False


class HelperListOut(Schema):
    can_manage: bool
    roles: list[str]
    helpers: list[HelperOut]


class HelperIn(Schema):
    label: str = ""
    endpoint_url: str
    roles: list[str] = []
    key: str | None = None


class HelperTryIn(Schema):
    model: str


class RunBindingOut(Schema):
    role: str
    kind: str = ""
    adapter_version: str = ""
    base_url: str = ""
    model: str = ""
    connection: str | None = None
    helper: str | None = None


class RunBindingIn(Schema):
    role: str = ""
    connection: str | None = None
    helper: str | None = None


class RunBindingListOut(Schema):
    bindings: list[RunBindingOut]
