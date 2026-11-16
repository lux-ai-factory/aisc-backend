"""A measure keeps a reference to the recorded call, not the conversation text.

A plugin may put the prompt it sent and the system's answer into a measure's dimensions. Those dimensions are
read by every viewer of the project's results and dashboards, so the engine stores, in place of the text under
a conversation key (prompt, response, conversation, answer, reply, request; any case), a reference to the call
the platform recorded: `trace:<test pid>#sha256=<hex digest of the UTF-8 text>`. The platform finds the call
again by that digest (run_trace.seqs_with_text), for the people who may read the trace. Every other dimension,
and an empty value, stays as the plugin sent it.
"""
from __future__ import annotations

import hashlib
import json
import uuid

CONVERSATION_KEYS = ("prompt", "response", "conversation", "answer", "reply", "request")
PREFIX = "trace:"


def reference(evaluation_plugin: uuid.UUID | str, text: str) -> str:
    return f"{PREFIX}{evaluation_plugin}#sha256={hashlib.sha256(text.encode('utf-8')).hexdigest()}"


def is_reference(value) -> bool:
    return isinstance(value, str) and value.startswith(PREFIX) and "#sha256=" in value


def referenced(dimensions: dict | None, evaluation_plugin: uuid.UUID | str) -> dict | None:
    """The dimensions with each conversation value replaced by its trace reference."""
    if not dimensions:
        return dimensions
    out = {}
    for key, value in dimensions.items():
        if (isinstance(key, str) and key.casefold() in CONVERSATION_KEYS and value not in ("", None)
                and not is_reference(value)):
            text = value if isinstance(value, str) else json.dumps(value, sort_keys=True, ensure_ascii=False)
            value = reference(evaluation_plugin, text)
        out[key] = value
    return out
