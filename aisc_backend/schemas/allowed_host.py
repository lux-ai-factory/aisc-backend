"""Schemas for the allowed internal hosts list (CON-01)."""
from typing import Any

from ninja import Schema


class AllowedHostIn(Schema):
    note: str | None = None


class AllowedHostOut(Schema):
    host: str
    note: str = ""
    updated_at: Any = None
    updated_by: str = ""
    #: Why this entry can no longer take effect (it names a loopback, metadata or
    #: deployment address), as the pilot shows it in red; None while it still may.
    denied: str | None = None


class AllowedHostListOut(Schema):
    #: Hosts the deployment allows for every project (`CONNECTIONS_ALLOWED_HOSTS`);
    #: not removable from a project page.
    floor: list[str] = []
    entries: list[AllowedHostOut] = []
    can_manage: bool = False
    rule: str = ""


class AllowedHostPutOut(Schema):
    host: str
    note: str = ""
    updated_at: Any = None
    updated_by: str = ""
