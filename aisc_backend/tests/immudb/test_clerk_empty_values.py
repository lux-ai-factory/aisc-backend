"""The audit clerk records an event whose optional fields are empty (no resource id, no source address), on a
real immudb: a missing value is stored as empty, not lost with the whole event.

The immudb here is a throwaway of this module's own: a container `aisc-t-immu-<hex>` of the stack's image on a
free loopback port (never 3322), removed afterwards. Its password is generated and never printed. Without
Docker the module skips."""
import secrets
import shutil
import socket
import subprocess
import time

from django.test import SimpleTestCase, override_settings

from aisc_backend.audit import clerk as clerk_mod

IMAGE = "codenotary/immudb:1.11.1"


def _free_port() -> int:
    while True:
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()
        if port != 3322:
            return port


def _docker() -> bool:
    return bool(shutil.which("docker")) and subprocess.run(
        ["docker", "image", "inspect", IMAGE], capture_output=True, timeout=30).returncode == 0


class ClerkEmptyValues(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        if not _docker():
            raise cls.skipTest(cls, f"Docker with {IMAGE} is needed for a throwaway immudb")
        cls.name = "aisc-t-immu-" + secrets.token_hex(4)
        cls.port = _free_port()
        cls.password = "Az9-" + secrets.token_hex(12)
        env = {"PATH": __import__("os").environ["PATH"], "IMMUDB_ADMIN_PASSWORD": cls.password}
        started = subprocess.run(["docker", "run", "-d", "--rm", "--name", cls.name, "-p",
                                  f"127.0.0.1:{cls.port}:3322", "-e", "IMMUDB_ADMIN_PASSWORD", IMAGE],
                                 capture_output=True, text=True, env=env, timeout=120)
        assert started.returncode == 0, "the throwaway immudb did not start"
        cls.addClassCleanup(subprocess.run, ["docker", "rm", "-f", cls.name], capture_output=True, timeout=60)
        deadline = time.monotonic() + 60
        while True:
            try:
                clerk_mod.ImmudbClient(f"127.0.0.1:{cls.port}").login("immudb", cls.password)
                break
            except Exception:  # noqa: BLE001 - not up yet
                if time.monotonic() > deadline:
                    raise AssertionError("the throwaway immudb did not accept its superuser within 60 s")
                time.sleep(0.5)

    def _clerk(self):
        settings = override_settings(IMMUDB_URL=f"127.0.0.1:{self.port}", IMMUDB_USER="immudb",
                                     IMMUDB_PASSWORD=self.password, IMMUDB_DATABASE="audittest")
        settings.enable()
        self.addCleanup(settings.disable)
        return clerk_mod.AuditClerk()

    def test_an_event_with_no_resource_id_and_no_source_address_is_recorded(self):
        clerk = self._clerk()
        try:
            clerk.write_event(actor="admin", action="delete", resource_type="component", resource_id=None,
                              source_ip=None, metadata={"name": "questions"})
        except Exception as exc:  # noqa: BLE001 - the refusal is the finding, said as a failure
            self.fail(f"the event was not recorded: {type(exc).__name__}: {exc}")
        [event] = clerk.list_events(limit=1)
        self.assertEqual((event["actor"], event["action"], event["resource_type"]), ("admin", "delete", "component"))
        self.assertIn(event["resource_id"], (None, ""))
        self.assertIn(event["source_ip"], (None, ""))
