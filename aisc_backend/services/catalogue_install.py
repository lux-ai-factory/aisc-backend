"""Catalogue one-click install — trust foundation (L3).

Pure, side-effect-free helpers used by the (future) install endpoint (L4) to make
sure we only ever install *exactly the right package from the right source* (E6):

- ``is_trusted_index`` — is a package index part of the trusted allowlist?
- ``pin_requirement`` — build a ``pkg==version`` requirement, refusing anything
  that is not a single frozen, exact version.

No I/O here: the allowlist is read either from the ``allowlist`` argument or from
``settings.CATALOGUE_TRUSTED_INDEXES`` *at call time* (never captured at import).
"""

from __future__ import annotations

import re
from urllib.parse import urlsplit

from django.conf import settings

# A single, frozen, exact version (PEP 440 subset): release segments plus the
# usual optional pre/post/dev/local suffixes. Deliberately strict — no range
# operators (>=, <=, ==, ~=, !=), no wildcard (*), no commas, no spaces.
_EXACT_VERSION_RE = re.compile(
    r"^"
    r"\d+(?:\.\d+)*"           # release: 1, 0.1, 1.2.3, ...
    r"(?:(?:a|b|rc)\d+)?"      # pre-release: a1, b2, rc1
    r"(?:\.post\d+)?"          # post-release: .post1
    r"(?:\.dev\d+)?"           # dev-release: .dev3
    r"(?:\+[a-zA-Z0-9]+(?:[.][a-zA-Z0-9]+)*)?"  # local: +abc.1
    r"\Z"  # end-of-string (not '$': '$' would match before a trailing newline)
)

# PEP 508 project name. '\Z' (not '$'): '$' would match before a trailing newline,
# letting "pkg\n" through and splitting into an unpinned "pkg" line downstream (E6).
_PACKAGE_NAME_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?\Z")


def _normalise_index(index_url: str) -> str | None:
    """Normalise an index URL for exact comparison.

    Lower-cases scheme and host, keeps the path, and guarantees a trailing slash.
    Only scheme + netloc + path are compared — query/fragment are ignored.
    Returns ``None`` when the URL is empty or has no scheme/host (never trusted).
    """
    if not index_url or not isinstance(index_url, str):
        return None
    parts = urlsplit(index_url.strip())
    if not parts.scheme or not parts.netloc:
        return None
    path = parts.path
    if not path.endswith("/"):
        path += "/"
    return f"{parts.scheme.lower()}://{parts.netloc.lower()}{path}"


def is_trusted_index(index_url: str, allowlist: list[str] | None = None) -> bool:
    """Return ``True`` iff ``index_url`` belongs to the trusted allowlist (E6).

    Comparison is on the normalised (scheme + host + path, trailing slash) form —
    never a substring match, and ``http`` is never equal to ``https``. The
    allowlist defaults to ``settings.CATALOGUE_TRUSTED_INDEXES`` when not given.
    """
    target = _normalise_index(index_url)
    if target is None:
        return False
    if allowlist is None:
        allowlist = getattr(settings, "CATALOGUE_TRUSTED_INDEXES", [])
    trusted = {n for n in (_normalise_index(u) for u in allowlist) if n is not None}
    return target in trusted


def pin_requirement(package_name: str, version: str) -> str:
    """Return ``f"{package_name}=={version}"`` for a single exact version (E6).

    Raises ``ValueError`` for anything that is not a frozen, exact version:
    empty/None, a range (``>=``, ``~=``, ...), a wildcard (``*``), or any
    dubious character. We never install a non-pinned version.
    """
    if not package_name or not isinstance(package_name, str):
        raise ValueError("package_name must be a non-empty string")
    if not _PACKAGE_NAME_RE.match(package_name):
        raise ValueError(f"invalid package name: {package_name!r}")
    if not version or not isinstance(version, str):
        raise ValueError("version must be a non-empty exact version string")
    if not _EXACT_VERSION_RE.match(version):
        raise ValueError(
            f"version {version!r} is not a frozen exact version "
            "(ranges, wildcards and operators are refused)"
        )
    return f"{package_name}=={version}"
