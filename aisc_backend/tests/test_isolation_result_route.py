"""Isolation 2026-09-25, open issue 7: the run-result route no longer leaks (I7.2, I16.5).

`GET /api/v1/plugins/{evaluation_plugin_pid}/evaluations/{evaluation_uuid}/result`
(routers/plugin.py, get_plugin_evaluation_results, a frozen Sean file) makes no
membership call: before isolation anybody with a valid token and the two uuids
read that run's measurements and visualisations, whatever project it was in.
It stays byte for byte as it is; the door closes it: the project is named in
X-AISC-Project, membership of that project is checked before its database is
opened, and the view's queries see only that database.

The world here is its own (not test_isolation_engine_db's): project A with
alice as owner and one finished run, project B with mallory as owner and
nothing of A. mallory has a valid token and knows A's two uuids. The plugin
loader is faked so the view can answer 200 for a member, which proves the
refusals below are the door's and the database's doing, not a broken route.

Needs the throwaway Postgres of test_isolation_engine_db.py (same environment);
without ENGINE_TEST_SUPERUSER_URL every test here skips.
"""
from __future__ import annotations

import os
import unittest
import unittest.mock as mock

from aisc_backend.tests.isolation_support import SUPERUSER_URL_VAR, ApiCaller, Cluster, database_name, superuser_url
from aisc_backend.tests.test_isolation_engine_db import SKIP_REASON, _migrate_projects, _seed

_WORLD: dict | None = None
_WORLD_ERROR: str | None = None
_CLUSTERS: list = []


def _build() -> dict:
    cluster = Cluster(superuser_url())
    _CLUSTERS.append(cluster)
    a = cluster.platform_project("A", {"alice": "owner"})
    b = cluster.platform_project("B", {"mallory": "owner"})
    for pid in (a, b):
        cluster.provision(pid)
        with cluster.connect(database_name(pid)) as conn:
            conn.execute("INSERT INTO project.system (number, name) VALUES (1, 'card v1')")
    migrated = _migrate_projects()
    if migrated.returncode != 0:
        raise AssertionError(f"I7.6: manage.py migrate_projects exited {migrated.returncode}")
    return {"cluster": cluster, "A": _seed(a, "A"), "B": b}


def tearDownModule():
    for cluster in _CLUSTERS:
        cluster.cleanup()


class _FakePlugin:
    def get_metrics(self):
        return ["accuracy"]

    def get_metric_visualizations(self, config):
        return []


@unittest.skipUnless(os.environ.get(SUPERUSER_URL_VAR), SKIP_REASON)
class TheResultRouteNoLongerLeaks(unittest.TestCase):
    def setUp(self):
        global _WORLD, _WORLD_ERROR
        if _WORLD_ERROR is not None:
            self.fail(_WORLD_ERROR)
        if _WORLD is None:
            try:
                _WORLD = _build()
            except Exception as exc:  # noqa: BLE001 - the reason every test fails
                _WORLD_ERROR = f"building the world failed: {type(exc).__name__}: {str(exc)[:300]}"
                self.fail(_WORLD_ERROR)
        self.w = _WORLD
        self.api = ApiCaller()
        self.addCleanup(self.api.stop)
        loader = mock.patch("aisc_backend.routers.plugin.plugin_loader.load_plugin", return_value=_FakePlugin())
        loader.start()
        self.addCleanup(loader.stop)
        a = self.w["A"]
        self.path = f"/api/v1/plugins/{a['evaluation_plugin_pid']}/evaluations/{a['evaluation_pid']}/result"

    def test_issue_7_a_member_of_the_project_reads_the_result(self):
        response = self.api.as_member("GET", self.path, self.w["A"]["platform"], subject="alice")
        self.assertEqual(response.status_code, 200, response.content[:300])
        self.assertIn("measurements", response.json())

    def test_issue_7_without_the_project_header_it_is_400(self):
        response = self.api.call("GET", self.path, {"Authorization": self.api.bearer("mallory")})
        self.assertEqual(response.status_code, 400)

    def test_issue_7_a_stranger_naming_the_runs_project_is_404(self):
        response = self.api.as_member("GET", self.path, self.w["A"]["platform"], subject="mallory")
        self.assertEqual(response.status_code, 404, "open issue 7: a stranger read another project's result")

    def test_issue_7_a_member_of_another_project_naming_her_own_is_404(self):
        response = self.api.as_member("GET", self.path, self.w["B"], subject="mallory")
        self.assertEqual(response.status_code, 404, "open issue 7: A's run was found through B's database")
