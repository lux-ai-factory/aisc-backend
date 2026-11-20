"""Putting proposals onto a form that may already have answers.

Two modes, and one rule that holds under both: a field the document says
nothing about keeps what it had. "Replace everything" means replace it with the
document, never with nothing, because a form emptied by an upload is the worst
thing this feature could do.
"""
from __future__ import annotations

from dataclasses import dataclass, field


class UnknownMode(ValueError):
    """Not one of the two modes, and guessing which was meant would be worse."""


#: What the person chose when the form was not empty.
MODES = ("empty", "replace")


@dataclass
class Merged:
    values: dict[str, str]
    #: fields this upload wrote
    filled: list[str] = field(default_factory=list)
    #: fields it left as they were, and why is implied by the mode
    kept: list[str] = field(default_factory=list)


def _blank(value) -> bool:
    return not isinstance(value, str) or not value.strip()


def merge(current: dict, proposals: dict, mode: str) -> Merged:
    if mode not in MODES:
        raise UnknownMode(f"a mode is one of {', '.join(MODES)}, not {mode!r}")

    values = {k: v for k, v in (current or {}).items()}
    result = Merged(values=values)
    for name, proposed in (proposals or {}).items():
        if _blank(proposed):
            # Nothing to say about this field. Never an erasure.
            if name in values and not _blank(values[name]):
                result.kept.append(name)
            continue
        if mode == "empty" and not _blank(values.get(name)):
            result.kept.append(name)
            continue
        values[name] = proposed.strip()
        result.filled.append(name)
    for name, value in (current or {}).items():
        if name not in proposals and not _blank(value):
            result.kept.append(name)
    result.filled.sort()
    result.kept = sorted(set(result.kept))
    return result
