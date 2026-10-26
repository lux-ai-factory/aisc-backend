"""The secret rules: which plugin configuration values are secrets, and how text is cleared of them.

Two rules, because the cost of a wrong guess differs:

- Ledger copies (`masked_for_ledger`): broad. Any key whose name says key, token, secret, password or credential
  holds only whether it is set, and any string shaped like a known key is replaced too. A copy that loses a
  harmless detail costs nothing.
- Stored configurations (`without_stored_secrets`): narrow. Only the last word or two of a name count, and a
  `key` or `token` value must look like one; a value that is wholly one key shape goes whatever its name. A
  secret field is dropped, never replaced by a placeholder the plugin would run with.

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

#: A key holding one of these words is a secret in a ledger copy (`Authorization` and `Cookie` as header fields).
LEDGER_SECRET_NAME = re.compile(r"key|token|secret|password|credential|authorization|cookie", re.IGNORECASE)
#: A value that looks like a credential after `Bearer`, `Basic`, `Token` or a bare `token=`: 16 characters or more
#: of the base64, hex and URL-safe alphabets, no space (`the bearer of bad news`, `token=EOF` are not secrets).
CREDENTIAL = r"[A-Za-z0-9+/=_.~-]{16,}"
#: Strings shaped like a known kind of key, wherever they are: provider key prefixes, JWTs, a credential-shaped
#: `Bearer` value, the credential of an `Authorization:` header (Basic, Token; group 1 keeps the header name), the
#: `user:password` of a URL and a whole PEM private-key block. Not caught: a base64-encoded key (other than a Basic
#: header's), a key split across lines, a key with no known prefix outside an assignment.
SECRET_SHAPE = re.compile(
    r"(?i)\bbearer\s+" + CREDENTIAL +
    r"|\bsk-[A-Za-z0-9_-]{16,}"
    r"|\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]*\.[A-Za-z0-9_-]*"
    r"|\bhf_[A-Za-z0-9]{20,}"
    r"|\bgh[pousr]_[A-Za-z0-9]{20,}"
    r"|\bAKIA[0-9A-Z]{16}\b"
    r"|\bxox[abpr]-[A-Za-z0-9-]{10,}"
    r"|\bAIza[0-9A-Za-z_-]{35}"
    r"|(\bauthorization\s*:\s*)(?:basic|token)\s+" + CREDENTIAL +
    r"|(?<=://)[^/\s:@]+:[^/\s@]+(?=@)"
    r"|-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY-----[\s\S]*?-----END (?:[A-Z0-9]+ )*PRIVATE KEY-----")
#: `password=...`, `api_key: "..."`, `api_key%3D...` and the like in text. Group 1 is the name, which may carry
#: any prefix ending in `_` or at a lower-to-upper case step (`judge_api_key`, `DB_PASSWORD`, `judgeApiKey`), but
#: nothing after it (`api_key_env=`, `password_min_length=` are not secrets); group 3 is the value, up to the next
#: whitespace or the closing quote. A bare `token` or `pwd` counts only before `=` (`unexpected token: }` and
#: `pwd: /home` are not secrets). The value is redacted only when it looks like a key (see `_key_like`), so
#: `password: it must contain a digit` stays. Not caught: a value that reads like a word (no digit, and under 16
#: characters or all in one case, such as `password=letmein`), and a quoted value of several words (a multi-word
#: passphrase such as `password = 'correct horse battery staple'`), which reads like prose.
SECRET_ASSIGNMENT = re.compile(
    r"(?i)\b((?:[a-z0-9-]*_|[a-z0-9]*(?-i:[a-z0-9])(?=(?-i:[A-Z])))?"
    r"(?:api[_-]?key|apikey|access[_-]?token|auth[_-]?token|refresh[_-]?token|secret[_-]?key"
    r"|(?:aws[_-]?)?secret[_-]?access[_-]?key|client[_-]?secret|password|passwd"
    r"|(?:token|pwd)(?=[\"']?\s*(?:=|%3d))))"
    r"([\"']?\s*(?:[=:]|%3d)\s*[\"']?)"
    r"((?!\[redacted\])(?:(?<=\")[^\"\s]+(?=\")|(?<=')[^'\s]+(?=')|[^\s\"',;]+))")

STRONG_LAST = {"password", "passwd", "passphrase", "secret", "credential", "credentials", "apikey", "authorization",
               "cookie"}
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
    """A plain string other than SECRET_SET, or a dict or list holding one at any depth (the whole value is then
    the secret: `api_keys: {openai: ...}`). A number is not: `max_tokens: 512` has the same form."""
    if isinstance(value, str):
        return value != "" and value != SECRET_SET
    if isinstance(value, dict):
        return any(_plaintext(inner) for inner in value.values())
    if isinstance(value, list):
        return any(_plaintext(item) for item in value)
    return False


def plaintext_secret_paths(value, _prefix: str = "") -> list[str]:
    """The paths (`a.b`, `items[0].token`) of the values a ledger copy must not hold: a secret-named key with a
    plain string, or with a dict or list holding one, and any string with a key shape. A listed key is not
    descended into."""
    found: list[str] = []
    if isinstance(value, dict):
        for name, inner in value.items():
            path = _path(_prefix, name)
            if LEDGER_SECRET_NAME.search(str(name)) and _plaintext(inner):
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
    """"strong" when the name ends in a secret word or pair (header fields such as Authorization and Cookie
    included), "weak" when it ends in key or token, else None."""
    words = name_words(name)
    if not words:
        return None
    if words[-1] in STRONG_LAST or tuple(words[-2:]) in STRONG_PAIRS:
        return "strong"
    if words[-1] in WEAK_LAST:
        return "weak"
    return None


def _holds_string(value) -> bool:
    if isinstance(value, str):
        return value != ""
    if isinstance(value, dict):
        return any(_holds_string(inner) for inner in value.values())
    if isinstance(value, list):
        return any(_holds_string(item) for item in value)
    return False


def _wholly_one_secret(value: str) -> bool:
    """The whole string is one key shape (`sk-...`, `Bearer ...`, a PEM private key): it is nothing but a secret."""
    return SECRET_SHAPE.fullmatch(value.strip()) is not None


def _stored_secret(kind: str | None, value) -> bool:
    if isinstance(value, dict):
        return kind == "strong" and _holds_string(value)
    if isinstance(value, list):
        if kind == "strong":
            return _holds_string(value)
        return any(_stored_secret(kind, item) for item in value if isinstance(item, str))
    if not isinstance(value, str) or not value:
        return False
    if _wholly_one_secret(value) or kind == "strong":
        return True
    return kind == "weak" and len(value) >= WEAK_MIN_LENGTH and not any(char.isspace() for char in value)


def stored_secret_changes(config, _prefix: str = ""):
    """(the configuration without its stored secrets, the dropped paths, the scrubbed paths).

    A field is dropped, never replaced, when its value is a non-empty string (or a dict or list holding one) under
    a strong name, a key-like string under a weak one, or a string that is wholly one key shape whatever its name.
    Any other string is scrubbed of key shapes and assignments, so a key pasted into a prompt goes and the prompt
    stays; its path is listed as scrubbed, since the key in it was stored all the same."""
    if isinstance(config, dict):
        out, dropped, scrubbed = {}, [], []
        for name, inner in config.items():
            path = _path(_prefix, name)
            if _stored_secret(stored_secret_name(str(name)), inner):
                dropped.append(path)
                continue
            out[name], below, cleared = stored_secret_changes(inner, path)
            dropped.extend(below)
            scrubbed.extend(cleared)
        return out, dropped, scrubbed
    if isinstance(config, list):
        out, dropped, scrubbed = [], [], []
        for index, item in enumerate(config):
            new, below, cleared = stored_secret_changes(item, f"{_prefix}[{index}]")
            out.append(new)
            dropped.extend(below)
            scrubbed.extend(cleared)
        return out, dropped, scrubbed
    if isinstance(config, str) and _prefix:
        text = scrub_text(config)
        return text, [], ([_prefix] if text != config else [])
    return config, [], []


def without_stored_secrets(config):
    """(the configuration without its stored secrets, the dropped paths): stored_secret_changes without the
    scrubbed paths."""
    new, dropped, _ = stored_secret_changes(config)
    return new, dropped


# Text


def _ordered_known(known: Iterable[str]) -> list[str]:
    values = {value for value in known if isinstance(value, str) and len(value) >= MIN_KNOWN_LENGTH}
    return sorted(values, key=lambda value: (-len(value), value))


_CREDENTIAL_VALUE = re.compile(CREDENTIAL)
_STRONG_TOKEN_NAME = re.compile(r"(?i)(?:access|auth|refresh)[_-]?token$")


def _key_like(name: str, value: str) -> bool:
    """Whether an assigned value looks like a key rather than a word: after a bare `token` it must look like a
    credential (16 characters or more); after any other name it holds a digit, or is 16 characters or more with
    upper- and lower-case letters (`hunter22x` and `wJalrXUtnFEMIK7MDENG` are; `it`, `None`, `/home/runner` are
    not)."""
    if name.lower().endswith("token") and not _STRONG_TOKEN_NAME.search(name):
        return _CREDENTIAL_VALUE.fullmatch(value) is not None
    if any(char.isdigit() for char in value):
        return True
    return len(value) >= 16 and value.lower() != value and value.upper() != value


def _redact_shape(match: re.Match) -> str:
    return (match.group(1) or "") + REDACTED


def _redact_assignment(match: re.Match) -> str:
    if not _key_like(match.group(1), match.group(3)):
        return match.group(0)
    return match.group(1) + match.group(2) + REDACTED


def _scrub_assignments(text: str) -> str:
    return SECRET_ASSIGNMENT.sub(_redact_assignment, text)


def scrub_text(text: str, known: Iterable[str] = ()) -> str:
    """`text` with each known value (8 characters or more, longest first), each key shape and the key-like value of
    each secret assignment replaced by REDACTED."""
    for value in _ordered_known(known):
        text = text.replace(value, REDACTED)
    text = SECRET_SHAPE.sub(_redact_shape, text)
    return _scrub_assignments(text)
