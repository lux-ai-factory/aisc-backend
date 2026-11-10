"""
The caller: the handful of claims the backend makes decisions with.

VERBATIM PORT of the reference implementation (`shared/identity/aisc_identity/caller.py` in
feat/unified-modules) so both codebases make the same decisions with the same identity shape.
Only deviation: the development fallback mirrors the reference's `development_caller`
(AUTH_ENABLED off -> a fixed fake identity), expressed as a function instead of a service config.

Kept to what is actually used, so a change in the identity provider touches one small shape
rather than every service that reads a token.
"""
from __future__ import annotations

from dataclasses import dataclass


class IdentityMissing(ValueError):
    """The claims verified but do not name anybody."""


@dataclass(frozen=True)
class Caller:
    #: Keycloak's stable subject. Every authorisation decision keys on this and
    #: never on the email, which a person can change.
    subject: str
    email: str | None
    username: str | None
    roles: tuple[str, ...]

    def has_role(self, role: str) -> bool:
        return role in self.roles


def caller_from_claims(claims: dict) -> Caller:
    """Who the verified token says is calling, keyed on the stable `sub`."""
    subject = claims.get("sub")
    if not subject:
        raise IdentityMissing("the token names no subject")
    realm = claims.get("realm_access") or {}
    return Caller(
        subject=subject,
        email=claims.get("email"),
        username=claims.get("preferred_username"),
        roles=tuple(realm.get("roles") or ()),
    )


def development_caller(roles: tuple[str, ...] = ()) -> Caller:
    """The obviously fake identity local work runs as when AUTH_ENABLED is off.

    Mirrors `development_caller` in the reference (`shared/identity/service.py`), so a local
    run without Keycloak still has a stable subject to key project membership on.
    """
    return Caller(
        subject="development",
        email="development@localhost",
        username="development",
        roles=roles,
    )
