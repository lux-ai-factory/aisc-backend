"""The REST adapter: a method, a path, a templated body, and where the answer is."""
from __future__ import annotations

from typing import Any

from .base import Answer, Request, Secret, context_of, json_path, parse_json, render, secret_headers


def _matches_refusal(refusal: dict, status: int, document: Any, text: str) -> bool:
    """A response that matches the connection's refusal rule is the system
    refusing, not an error."""
    criteria = 0
    statuses = refusal.get("status")
    if statuses is not None:
        criteria += 1
        if status not in (statuses if isinstance(statuses, list) else [statuses]):
            return False
    match = refusal.get("match")
    if isinstance(match, dict) and match:
        criteria += 1
        if not all(json_path(document, path) == expected for path, expected in match.items()):
            return False
    path = refusal.get("path")
    if path:
        criteria += 1
        if json_path(document, path) is None and not match:
            return False
    if criteria == 0:
        return status >= 400
    return True


class RestAdapter:
    kind = "rest"
    label = "REST API"

    def build_request(
        self, connection, secret: Secret, *, input: Any, history: list | None = None,
        params: dict | None = None,
    ) -> Request:
        context = context_of(input, history, params)
        base = (connection.base_url or "").rstrip("/")
        path = (connection.path or "").lstrip("/")
        headers = {"Content-Type": "application/json"}
        headers.update(connection.headers or {})
        headers.update(secret_headers(connection, secret))
        return Request(
            method=(connection.method or "POST").upper(),
            url=f"{base}/{path}" if path else base,
            headers=headers,
            json_body=render(connection.body_template or {}, context),
        )

    def parse_response(self, status: int, text: str, connection) -> Answer:
        document = parse_json(text)
        refusal = connection.refusal or None
        if isinstance(refusal, dict) and _matches_refusal(refusal, status, document, text):
            path = refusal.get("path")
            reason = json_path(document, path) if path else None
            return Answer(ok=True, refused=True, refusal_reason=reason or refusal.get("reason") or None)
        if status >= 400:
            return Answer(ok=False, error="http_status", detail=f"the system answered HTTP {status}")
        if connection.response_path:
            answer = json_path(document, connection.response_path)
        else:
            answer = document if document is not None else text
        if answer is None:
            return Answer(
                ok=False, error="no_answer",
                detail=f"nothing at {connection.response_path!r} in the response",
            )
        return Answer(ok=True, answer=answer)
