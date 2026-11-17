"""The OpenAI-compatible adapter: POST {base}/chat/completions."""
from __future__ import annotations

from typing import Any

from .base import Answer, Request, Secret, secret_headers


class OpenAiAdapter:
    kind = "openai"
    label = "OpenAI-compatible"

    def build_request(
        self, connection, secret: Secret, *, input: Any, history: list | None = None,
        params: dict | None = None,
    ) -> Request:
        base = (connection.base_url or "").rstrip("/")
        messages: list[dict] = list(history or [])
        messages.append({"role": "user", "content": input})
        body: dict[str, Any] = {"model": connection.model, "messages": messages}
        if connection.supports_logprobs:
            body["logprobs"] = True
        headers = {"Content-Type": "application/json"}
        headers.update(connection.headers or {})
        headers.update(secret_headers(connection, secret))
        return Request(method="POST", url=f"{base}/chat/completions", headers=headers, json_body=body)

    def parse_response(self, status: int, text: str, connection) -> Answer:
        if status >= 400:
            return Answer(ok=False, error="http_status", detail=f"the endpoint answered HTTP {status}")
        from .base import parse_json

        document = parse_json(text)
        if not isinstance(document, dict) or not document.get("choices"):
            return Answer(ok=False, error="no_answer", detail="the response carried no choices")
        choice = document["choices"][0]
        message = choice.get("message") or {}
        answer = message.get("content")
        if answer is None and message.get("tool_calls"):
            answer = message["tool_calls"]
        if answer is None:
            return Answer(ok=False, error="no_answer", detail="the choice carried no content")
        return Answer(
            ok=True,
            answer=answer,
            capabilities={
                "tools": bool(message.get("tool_calls")),
                "logprobs": bool(choice.get("logprobs")),
            },
        )
