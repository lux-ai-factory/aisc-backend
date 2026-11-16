"""The secret rules: which plugin configuration values are secrets, and how text is cleared of them.

The rules live in one module shared by every Python service, aisc_redact (shared/identity), held to the shared
vectors redaction-cases.json; this module keeps the engine's names for them. See aisc_redact for the ledger-copy
rule (`masked_for_ledger`), the stored-configuration rule (`stored_secret_changes`, `without_stored_secrets`) and
the text rule (`scrub_text`), and for what the shape rule does not catch.
"""
from __future__ import annotations

from aisc_redact import (  # noqa: F401 (the engine's names for the shared rule)
    CREDENTIAL,
    MIN_KNOWN_LENGTH,
    REDACTED,
    SECRET_ASSIGNMENT,
    SECRET_SET,
    SECRET_SHAPE,
    STRONG_LAST,
    STRONG_PAIRS,
    WEAK_LAST,
    WEAK_MIN_LENGTH,
    _holds_string,
    _path,
    _stored_secret,
    holds_plaintext_secret,
    masked_for_ledger,
    name_words,
    plaintext_secret_paths,
    scrub_text,
    stored_secret_changes,
    stored_secret_name,
    without_stored_secrets,
)
