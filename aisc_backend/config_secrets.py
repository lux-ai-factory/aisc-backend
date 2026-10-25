"""The secret rules: which plugin configuration values are secrets, and how text is cleared of them.

Two rules, because the cost of a wrong guess differs:

- Ledger copies (`masked_for_ledger`): broad. Any key whose name says key, token, secret, password or credential
  holds only whether it is set, and any string shaped like a known key is replaced too. A copy that loses a
  harmless detail costs nothing.
- Stored configurations (`without_stored_secrets`): narrow. Only the last word or two of a name count, strings
  only, and a `key` or `token` value must look like one. A dropped field changes what the plugin runs with.

`scrub_text` clears failure text: the values a caller knows it handed out, then the key shapes, then the value of
a `password=...` style assignment.

Stdlib only. The platform (platform_service/ledger/config_secrets.py) and the worker (aisc_eval/scrub.py) carry
copies of these rules, and a root test keeps the three equal.
"""
from __future__ import annotations

import re
from typing import Iterable

#: What a secret field's value is in a ledger copy: whether it is set, never what it is.
SECRET_SET = "set"
#: What a secret in text becomes.
REDACTED = "[redacted]"
#: A known secret shorter than this is never searched for (it would match ordinary words).
MIN_KNOWN_LENGTH = 8
#: A value under a name ending in key or token is a secret only if it is at least this long, with no whitespace.
WEAK_MIN_LENGTH = 16

#: A key holding one of these words is a secret in a ledger copy.
LEDGER_SECRET_NAME = re.compile(r"key|token|secret|password|credential", re.IGNORECASE)
#: Strings shaped like a known kind of key, wherever they are.
SECRET_SHAPE = re.compile(
    r"(?i)\bbearer\s+\S+"
    r"|\bsk-[A-Za-z0-9_-]{16,}"
    r"|\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]*\.[A-Za-z0-9_-]*"
    r"|\bhf_[A-Za-z0-9]{20,}"
    r"|\bgh[pousr]_[A-Za-z0-9]{20,}"
    r"|\bAKIA[0-9A-Z]{16}\b"
    r"|\bxox[abpr]-[A-Za-z0-9-]{10,}"
    r"|\bAIza[0-9A-Za-z_-]{35}")
#: `password=...`, `api_key: "..."` and the like in text: group 3 is the value.
SECRET_ASSIGNMENT = re.compile(
    r"(?i)\b(api[_-]?key|apikey|access[_-]?token|auth[_-]?token|refresh[_-]?token|secret[_-]?key"
    r"|client[_-]?secret|password|passwd)"
    r"([\"']?\s*[=:]\s*[\"']?)"
    r"((?!\[redacted\])[^\s\"',;]+)")

STRONG_LAST = {"password", "passwd", "passphrase", "secret", "credential", "credentials", "apikey"}
STRONG_PAIRS = {("api", "key"), ("access", "key"), ("private", "key"), ("secret", "key"), ("access", "token"),
                ("auth", "token"), ("api", "token"), ("bearer", "token"), ("refresh", "token"), ("session", "token")}
WEAK_LAST = {"key", "token"}

_EMPTY = (None, "", [], {})
_WORD_SPLIT = re.compile(r"[^A-Za-z0-9]+")
_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")


def _path(prefix: str, name) -> str:
    return f"{prefix}.{name}" if prefix else str(name)


# Ledger copies


def masked_for_ledger(value):
    """`value` as a ledger copy may hold it: a secret-named key's value is SECRET_SET (None when empty), and any
    string with a key shape is SECRET_SET. Recursive over dicts and lists; anything else is kept."""
    if isinstance(value, dict):
        out = {}
        for name, inner in value.items():
            if LEDGER_SECRET_NAME.search(str(name)):
                out[name] = SECRET_SET if inner not in _EMPTY else None
            else:
                out[name] = masked_for_ledger(inner)
        return out
    if isinstance(value, list):
        return [masked_for_ledger(item) for item in value]
    if isinstance(value, str) and SECRET_SHAPE.search(value):
        return SECRET_SET
    return value


def _plaintext(value) -> bool:
    return isinstance(value, str) and value != "" and value != SECRET_SET


def plaintext_secret_paths(value, _prefix: str = "") -> list[str]:
    """The paths (`a.b`, `items[0].token`) of the values a ledger copy must not hold: a secret-named key with a
    plain string (or a list holding one), and any string with a key shape. A listed key is not descended into."""
    found: list[str] = []
    if isinstance(value, dict):
        for name, inner in value.items():
            path = _path(_prefix, name)
            if LEDGER_SECRET_NAME.search(str(name)) and (
                    _plaintext(inner) or (isinstance(inner, list) and any(_plaintext(item) for item in inner))):
                found.append(path)
            else:
                found.extend(plaintext_secret_paths(inner, path))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            found.extend(plaintext_secret_paths(item, f"{_prefix}[{index}]"))
    elif isinstance(value, str) and SECRET_SHAPE.search(value):
        found.append(_prefix)
    return found


def holds_plaintext_secret(value) -> bool:
    return bool(plaintext_secret_paths(value))


# Stored configurations


def name_words(name: str) -> list[str]:
    """`judgeApiKey` -> judge, api, key; `hf_token` -> hf, token."""
    words: list[str] = []
    for part in _WORD_SPLIT.split(name):
        words.extend(piece.lower() for piece in _CAMEL.split(part) if piece)
    return words


def stored_secret_name(name: str) -> str | None:
    """"strong" when the name ends in a secret word or pair, "weak" when it ends in key or token, else None."""
    words = name_words(name)
    if not words:
        return None
    if words[-1] in STRONG_LAST or tuple(words[-2:]) in STRONG_PAIRS:
        return "strong"
    if words[-1] in WEAK_LAST:
        return "weak"
    return None


def _stored_secret(kind: str | None, value) -> bool:
    if not isinstance(value, str) or not value or kind is None:
        return False
    if kind == "strong":
        return True
    return len(value) >= WEAK_MIN_LENGTH and not any(char.isspace() for char in value)


def without_stored_secrets(config, _prefix: str = ""):
    """(the configuration without its stored secrets, the dropped paths). A secret is a non-empty string (or a
    list holding one) under a strong name, or under a weak one when it looks like a key. Any other string is
    scrubbed of key shapes and assignments, so a key pasted into a prompt goes and the prompt stays."""
    if isinstance(config, dict):
        out, dropped = {}, []
        for name, inner in config.items():
            path = _path(_prefix, name)
            kind = stored_secret_name(str(name))
            items = inner if isinstance(inner, list) else [inner]
            if any(_stored_secret(kind, item) for item in items):
                dropped.append(path)
                continue
            out[name], below = without_stored_secrets(inner, path)
            dropped.extend(below)
        return out, dropped
    if isinstance(config, list):
        out, dropped = [], []
        for index, item in enumerate(config):
            new, below = without_stored_secrets(item, f"{_prefix}[{index}]")
            out.append(new)
            dropped.extend(below)
        return out, dropped
    if isinstance(config, str) and _prefix:
        return scrub_text(config), []
    return config, []


# Text


def _ordered_known(known: Iterable[str]) -> list[str]:
    values = {value for value in known if isinstance(value, str) and len(value) >= MIN_KNOWN_LENGTH}
    return sorted(values, key=lambda value: (-len(value), value))


def _redact_assignment(match: re.Match) -> str:
    return match.group(1) + match.group(2) + REDACTED


def scrub_text(text: str, known: Iterable[str] = ()) -> str:
    """`text` with each known value (8 characters or more, longest first), each key shape and the value of each
    secret assignment replaced by REDACTED."""
    for value in _ordered_known(known):
        text = text.replace(value, REDACTED)
    text = SECRET_SHAPE.sub(REDACTED, text)
    return SECRET_ASSIGNMENT.sub(_redact_assignment, text)
