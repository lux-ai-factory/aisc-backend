"""The dashboard's Redis is its own, password-protected and unpublished (ANA-01).

The pilot's rule, ported (``scripts/tests/test_dashboard_redis.py``): Superset's
RedisCache unpickles what it reads, so a cache shared with anything that runs
plugin code is an execution path into a process that holds every project's read
credentials. Its cache must therefore be a Redis of its own — a password read
from the container's environment (never written into the command line, where `ps`
would show it), on a network no service running plugin code joins, publishing no
port — and the stack must refuse to resolve without that password.

Two readings of one file, because either alone can be fooled: the compose file's
own text, and (when the `docker compose` CLI is present) the model it resolves,
which is what a deployment actually starts. The CLI does not need a daemon for
`config`, so this runs here and in CI.
"""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import unittest

import yaml
from django.test import SimpleTestCase

#: the parent repository, whose compose file defines the deployment
ROOT = Path(__file__).resolve().parents[4]
COMPOSE = ROOT / "docker-compose.yml"
SERVICE = "dashboard-redis"
#: services that run plugin code (or are the cache they share): never on its network
NOT_ON_ITS_NETWORK = ("aisc-eval-worker", "aisc-eval-flower", "redis", "aisc-backend", "caddy")
PASSWORD = "the-dashboard-redis-password"
HAVE_COMPOSE = shutil.which("docker") is not None


def _compose_config(*extra: str, password: str | None = PASSWORD):
    """`docker compose config` for this file, with a clean environment."""
    env = {"PATH": "/usr/bin:/bin:/usr/local/bin", "HOME": str(Path.home())}
    if password is not None:
        env["DASHBOARD_REDIS_PASSWORD"] = password
    return subprocess.run(
        ["docker", "compose", "-f", str(COMPOSE), *extra, "config"],
        capture_output=True, text=True, timeout=120, env=env, cwd=str(ROOT),
    )


class ComposeFileTextTest(SimpleTestCase):
    """The file itself: what a reviewer reads, without relying on the CLI."""

    def setUp(self):
        self.text = COMPOSE.read_text()
        self.model = yaml.safe_load(self.text)
        self.service = self.model["services"][SERVICE]

    def test_the_dashboard_redis_is_opt_in_and_never_started_by_default(self):
        self.assertEqual(self.service.get("profiles"), ["analytics"])
        self.assertNotIn(SERVICE, self.model.get("networks") or {})

    def test_it_asks_for_a_password_from_its_environment(self):
        env = self.service["environment"]
        self.assertIn("${DASHBOARD_REDIS_PASSWORD:?", env["DASHBOARD_REDIS_PASSWORD"])

    def test_the_password_reaches_the_server_through_a_config_file(self):
        """`redis-server --requirepass <value>` would put it in `ps` for everyone."""
        command = self.service["command"]
        command = " ".join(command) if isinstance(command, list) else str(command)
        self.assertEqual(self.service.get("entrypoint"), ["/bin/sh", "-c"])
        self.assertIn("requirepass", command)
        # the value is read inside the container, never written into the command
        self.assertIn('"$$DASHBOARD_REDIS_PASSWORD"', command)
        self.assertNotIn(PASSWORD, command)

    def test_it_publishes_nothing(self):
        self.assertNotIn("ports", self.service)

    def test_no_service_running_plugin_code_shares_its_network(self):
        networks = set(self.service["networks"])
        self.assertEqual(networks, {"dashboard"})
        for name, service in self.model["services"].items():
            if name == SERVICE:
                continue
            self.assertFalse(
                networks & set(service.get("networks") or []),
                f"{name} shares the dashboard's Redis network",
            )


@unittest.skipUnless(HAVE_COMPOSE, "the docker compose CLI is not installed")
class ResolvedComposeTest(SimpleTestCase):
    """The model the CLI resolves: what a deployment starts."""

    def test_without_the_password_the_stack_refuses_to_resolve(self):
        result = _compose_config(password=None)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("DASHBOARD_REDIS_PASSWORD", result.stderr)

    def test_the_default_profile_does_not_include_it(self):
        result = _compose_config(password=PASSWORD)
        self.assertEqual(result.returncode, 0, result.stderr[-2000:])
        services = yaml.safe_load(result.stdout)["services"]
        self.assertNotIn(SERVICE, services)

    def test_the_analytics_profile_resolves_it_isolated_and_unpublished(self):
        result = _compose_config("--profile", "analytics", password=PASSWORD)
        self.assertEqual(result.returncode, 0, result.stderr[-2000:])
        services = yaml.safe_load(result.stdout)["services"]
        service = services[SERVICE]

        self.assertEqual(service["environment"]["DASHBOARD_REDIS_PASSWORD"], PASSWORD)
        command = service["command"]
        command = " ".join(command) if isinstance(command, list) else str(command)
        self.assertIn("requirepass", command)
        self.assertNotIn(PASSWORD, command)
        self.assertNotIn("ports", service)

        on_it = {name for name, other in services.items()
                 if set(other.get("networks") or {}) & set(service["networks"])}
        self.assertEqual(on_it, {SERVICE})
        for name in NOT_ON_ITS_NETWORK:
            self.assertNotIn(name, on_it)
        self.assertNotIn("backend", service["networks"].keys())

    def test_two_caches_two_volumes_two_networks(self):
        """The eval cache and the dashboard's cache are separate containers."""
        result = _compose_config("--profile", "analytics", password=PASSWORD)
        services = yaml.safe_load(result.stdout)["services"]
        shared, dashboard = services["redis"], services[SERVICE]

        self.assertNotEqual(shared["image"] + "::" + str(shared.get("volumes")),
                            dashboard["image"] + "::" + str(dashboard.get("volumes")))
        shared_volumes = {v["source"] for v in shared.get("volumes") or []
                          if isinstance(v, dict)}
        dashboard_volumes = {v["source"] for v in dashboard.get("volumes") or []
                             if isinstance(v, dict)}
        self.assertTrue(shared_volumes and dashboard_volumes)
        self.assertEqual(shared_volumes & dashboard_volumes, set())
        self.assertNotIn("dashboard", shared["networks"])
        # and the shared cache does not carry the dashboard's password
        self.assertNotIn("DASHBOARD_REDIS_PASSWORD",
                         (shared.get("environment") or {}))
