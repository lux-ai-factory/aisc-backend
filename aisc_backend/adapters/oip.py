"""
The Open Inference Protocol adapter: POST {base}/v2/models/{model}/infer.

OIP (KServe v2) carries inputs as typed tensors; text goes in a BYTES input,
base64-encoded, and the answer comes back the same way.
"""
from __future__ import annotations

import base64
import uuid
from typing import Any

from .base import Answer, Request, Secret, parse_json, secret_headers


class OipAdapter:
    kind = "oip"
    label = "Open Inference Protocol"

    def build_request(
        self, connection, secret: Secret, *, input: Any, history: list | None = None,
        params: dict | None = None,
    ) -> Request:
        base = (connection.base_url or "").rstrip("/")
        text = input if isinstance(input, str) else str(input)
        encoded = base64.b64encode(text.encode()).decode()
        body = {
            "id": str(uuid.uuid4()),
            "inputs": [
                {"name": "text", "shape": [1], "datatype": "BYTES", "data": [encoded]},
            ],
        }
        headers = {"Content-Type": "application/json"}
        headers.update(connection.headers or {})
        headers.update(secret_headers(connection, secret))
        return Request(
            method="POST",
            url=f"{base}/v2/models/{connection.model}/infer",
            headers=headers,
            json_body=body,
        )

    def parse_response(self, status: int, text: str, connection) -> Answer:
        if status >= 400:
            return Answer(ok=False, error="http_status", detail=f"the server answered HTTP {status}")
        document = parse_json(text)
        outputs = (document or {}).get("outputs") if isinstance(document, dict) else None
        if not outputs:
            return Answer(ok=False, error="no_answer", detail="the response carried no outputs")
        output = outputs[0] or {}
        data = output.get("data")
        value = data[0] if isinstance(data, list) and data else data
        if output.get("datatype") == "BYTES" and isinstance(value, str):
            try:
                value = base64.b64decode(value).decode()
            except (ValueError, UnicodeDecodeError):
                pass
        return Answer(ok=True, answer=value)
