"""
A local mock target reached the way the pilot's mock targets are (CON-01).

A connection may not call loopback — that is the platform itself — so a test that
exercises the real call path binds its target on every interface and addresses it by
the machine's own address, then lists that address the way an owner would
(Manage → Components and connections → Allowed internal hosts). A test asserts the
same call is refused *before* it is listed too, so a smoke test cannot quietly depend
on a hole in the guard.

On a host with only loopback there is no address to reach a target on the way the
deployment does; such a run skips, and says so.
"""
from __future__ import annotations

import socket
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Iterator

from aisc_backend.models.connection import AllowedHost
from aisc_backend.models.project import Project


@dataclass(frozen=True)
class Target:
    """Where a mock target answers, and the allowlist key that reaches it."""

    url: str
    entry: str


def local_address() -> str | None:
    """This machine's own address, or None when it only has loopback.

    The UDP connect sends nothing; it asks the routing table which local address a
    packet to the public network would leave from.
    """
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("192.0.2.1", 9))  # TEST-NET-1, reserved and unrouted
            address = sock.getsockname()[0]
    except OSError:
        return None
    return address if not address.startswith("127.") else None


@contextmanager
def local_target(
    handler: type[BaseHTTPRequestHandler], *, suffix: str = ""
) -> Iterator[Target]:
    """Serve `handler` on every interface and yield the address to reach it by."""
    address = local_address()
    server = HTTPServer(("0.0.0.0", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host = address or "127.0.0.1"
        yield Target(
            url=f"http://{host}:{server.server_port}{suffix}",
            entry=f"{host}:{server.server_port}",
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


async def allow(project: Project, target: Target, note: str = "mock target") -> AllowedHost:
    """List the target's address, as an owner would on the page."""
    return await AllowedHost.objects.acreate(
        project=project, name=target.entry, description="",
        host=target.entry, note=note, updated_by="development",
    )
