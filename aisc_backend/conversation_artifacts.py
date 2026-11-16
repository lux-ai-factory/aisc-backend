"""Which artifacts hold a conversation with the system under test, and who may open them.

A conversation is the prompts sent to the system and its answers: personal data as often as not. An artifact
that holds one reaches the project's owners and editors only; a viewer still sees that it exists, not what it
says. An artifact holds one when the worker said so at upload (its record of every call and the plugin's
execution log), or when its name says so: conversation, transcript, calls, chat or execution.log.

The name rule misses a plugin file that holds prompts and answers under another name (verdicts.csv, a
per-prompt CSV): such a file is marked only when the worker or the plugin announces it.
"""
from __future__ import annotations

import re

#: names that say a file holds a conversation, anywhere in the name and in any case
NAMES = re.compile(r"(conversation|transcript|calls|chat|execution\.log)", re.IGNORECASE)


def named_like_one(name: str | None) -> bool:
    return bool(name) and NAMES.search(name) is not None


def needed_role(artifact) -> str:
    """The role a person needs to open this artifact's content."""
    return "editor" if getattr(artifact, "holds_conversation", False) else "viewer"
