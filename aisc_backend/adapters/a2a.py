"""
The A2A adapter: JSON-RPC `message/send` to the agent's interface.

The interface URL is the base URL plus the agent's JSON-RPC path; the agent's
card (its `.well-known` document) names that path, so `discover` reads it and the
`/test` endpoint uses it to fill a new connection.
"""
from __future__ import annotations

import json
import uuid
from typing import Any

from .base import Answer, Request, Secret, parse_json, secret_headers

#: The agent-card document per A2A version.
AGENT_CARD_PATH = {
    "1.0": "/.well-known/agent-card.json",
    "0.3": "/.well-known/agent.json",
}


def agent_card_url(base_url: str, protocol_version: str = "") -> str:
    base = (base_url or "").rstrip("/")
    path = AGENT_CARD_PATH.get(protocol_version or "1.0", AGENT_CARD_PATH["1.0"])
    return f"{base}{path}"


def _text_of(result: dict) -> Any:
    for artifact in result.get("artifacts") or []:
        for part in artifact.get("parts") or []:
            if part.get("text") is not None:
                return part["text"]
    message = (result.get("status") or {}).get("message") or {}
    for part in message.get("parts") or []:
        if part.get("text") is not None:
            return part["text"]
    return None


class A2aAdapter:
    kind = "a2a"
    label = "A2A agent"

    def build_request(
        self, connection, secret: Secret, *, input: Any, history: list | None = None,
        params: dict | None = None,
    ) -> Request:
        base = (connection.base_url or "").rstrip("/")
        path = (connection.path or "").lstrip("/")
        text = input if isinstance(input, str) else json.dumps(input)
        body = {
            "jsonrpc": "2.0",
            "id": str(uuid.uuid4()),
            "method": "message/send",
            "params": {
                "message": {
                    "role": "user",
                    "parts": [{"kind": "text", "text": text}],
                    "messageId": str(uuid.uuid4()),
                }
            },
        }
        headers = {"Content-Type": "application/json"}
        headers.update(connection.headers or {})
        headers.update(secret_headers(connection, secret))
        return Request(
            method="POST",
            url=f"{base}/{path}" if path else base,
            headers=headers,
            json_body=body,
        )

    def parse_response(self, status: int, text: str, connection) -> Answer:
        if status >= 400:
            return Answer(ok=False, error="http_status", detail=f"the agent answered HTTP {status}")
        document = parse_json(text)
        if not isinstance(document, dict):
            return Answer(ok=False, error="no_answer", detail="the agent's answer was not JSON")
        if document.get("error"):
            error = document["error"] or {}
            return Answer(ok=False, error="rpc_error", detail=error.get("message", "the agent refused the call"))
        result = document.get("result") or {}
        answer = _text_of(result)
        if answer is None:
            answer = result if result else None
        if answer is None:
            return Answer(ok=False, error="no_answer", detail="the agent's result carried no text")
        return Answer(ok=True, answer=answer)
