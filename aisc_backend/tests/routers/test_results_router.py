"""
Router tests for scoped results and the dashboard bridge (ANA-01).

A stranger gets 404; the selected AI-card version scopes the run counts; two
projects never see each other's runs; a locally served model's run stays distinct;
the charts are the visualizer's own aggregate over the scoped runs; and the
dashboard links are *signed and scoped* — built only when a base URL and a key are
configured, and verifiable for exactly one project, version and tool.
"""
from datetime import timedelta

from django.test import TestCase, override_settings
from django.utils import timezone
from ninja.testing import TestAsyncClient

from aisc_backend.models.ai_system import AIComponent, AISystem
from aisc_backend.models.connection import Connection, Helper, RunBinding
from aisc_backend.models.evaluation import Evaluation, EvaluationPlugin
from aisc_backend.models.plugin import EvaluationInput, Plugin, PluginConfig
from aisc_backend.models.project import Project
from aisc_backend.models.project_member import MembershipRole, ProjectMember
from aisc_backend.models.qualification import System, SystemVersion
from aisc_backend.models.measure import Measurement
from aisc_backend.models.metric import Metric
from aisc_backend.models.observation import Observation
from aisc_backend.routers.project import router as project_router
from aisc_backend.routers.results import router as results_router
from aisc_backend.services import dashboard_link

client = TestAsyncClient(results_router)


async def _project(name="results-project", *, member=True):
    project = await Project.objects.acreate(name=name, description="", status="Ready")
    if member:
        await ProjectMember.objects.acreate(
            project=project, subject="development", role=MembershipRole.Owner
        )
    return project


async def _component(project):
    system, _ = await AISystem.objects.aget_or_create(
        project=project, defaults={"name": "assessed", "description": ""}
    )
    return await AIComponent.objects.acreate(
        system=system, name="the model", description="", data="s3://m",
        component_type="model",
    )


async def _plugin(project, name):
    return await Plugin.objects.acreate(
        project=project, name=name, package_name=f"aisc-plugin-{name}", version="1.0",
        display_name=name.title(), description="",
    )


async def _run(project, plugin, *, when, status="Done", component=None, local=False,
               connection=None, helper=None):
    config = await PluginConfig.objects.acreate(
        plugin=plugin, name="c", description="", config={}
    )
    evaluation = await Evaluation.objects.acreate(project=project, status=status)
    await Evaluation.objects.filter(pk=evaluation.pk).aupdate(created_at=when)
    run = await EvaluationPlugin.objects.acreate(
        evaluation=evaluation, plugin_config=config, status=status,
        finished_at=when,
    )
    await EvaluationInput.objects.acreate(
        evaluation_plugin=run, component=component, name="target", description="",
        value={"local_model": {"path": "/m"}} if local else {"target": "card"},
    )
    if connection is not None:
        await RunBinding.objects.acreate(
            evaluation_plugin=run, role="", connection=connection, kind=connection.kind,
            adapter_version="1", base_url=connection.base_url,
        )
    if helper is not None:
        await RunBinding.objects.acreate(
            evaluation_plugin=run, role="judge", helper=helper, kind="openai",
            adapter_version="1", model="gpt-4o-mini",
        )
    return evaluation, run


async def _measurements(evaluation, metric_name, scores):
    """Real observations and measurements for an evaluation, as the engine writes them."""
    observation = await Observation.objects.acreate(
        observer="engine", tool="aisc-plugin-fairness::fairness (v1.0)", evaluation=evaluation
    )
    metric = await Metric.objects.acreate(
        name=metric_name, description="", type_spec="float"
    )
    for index, score in enumerate(scores):
        await Measurement.objects.acreate(
            observation=observation, metric=metric, unit="ratio",
            time=timezone.now() + timedelta(seconds=index), score=score,
        )
    return observation


async def _versions(project):
    system = await System.objects.acreate(
        project=project, name="assessed", description=""
    )
    now = timezone.now()
    v1 = await SystemVersion.objects.acreate(
        project=project, system=system, number=1, name="assessed",
    )
    await SystemVersion.objects.filter(pk=v1.pk).aupdate(created_at=now - timedelta(days=2))
    v2 = await SystemVersion.objects.acreate(
        project=project, system=system, number=2, name="assessed",
    )
    await SystemVersion.objects.filter(pk=v2.pk).aupdate(created_at=now - timedelta(days=1))
    return system, v1, v2


class ResultsRouterTest(TestCase):

    async def test_stranger_gets_404(self):
        project = await _project("stranger", member=False)
        resp = await client.get(f"/{project.pid}/results")
        self.assertEqual(resp.status_code, 404)

    async def test_version_scopes_run_counts_and_greyed_tools(self):
        project = await _project()
        _system, v1, v2 = await _versions(project)
        now = timezone.now()
        used = await _plugin(project, "fairness")
        await _plugin(project, "robustness")  # installed, never run -> greyed
        component = await _component(project)

        await _run(project, used, when=now - timedelta(days=2) + timedelta(hours=1),
                   status="Done", component=component)
        await _run(project, used, when=now - timedelta(hours=2), status="Failed",
                   component=component, local=True)

        latest = (await client.get(f"/{project.pid}/results")).json()
        self.assertEqual(latest["version"]["number"], 2)
        self.assertEqual([t["plugin_name"] for t in latest["tools"]], ["fairness"])
        tool = latest["tools"][0]
        self.assertEqual((tool["executed"], tool["failed"], tool["running"]), (0, 1, 0))
        self.assertTrue(tool["local"])
        self.assertEqual(tool["local_runs"], 1)
        self.assertEqual([g["plugin_name"] for g in latest["greyed"]], ["robustness"])

        first = (await client.get(f"/{project.pid}/results?version={v1.pid}")).json()
        self.assertEqual(first["version"]["number"], 1)
        tool = first["tools"][0]
        self.assertEqual((tool["executed"], tool["failed"], tool["running"]), (1, 0, 0))
        self.assertFalse(tool["local"])
        # the v2 run's local flag never leaks into the v1 window
        self.assertEqual(tool["local_runs"], 0)

    async def test_helpers_and_connections_come_from_the_run_bindings(self):
        project = await _project()
        _system, v1, _v2 = await _versions(project)
        now = timezone.now()
        used = await _plugin(project, "fairness")
        component = await _component(project)
        connection = await Connection.objects.acreate(
            project=project, name="judge-endpoint", description="", label="Judge",
            kind="openai", base_url="https://api.example.com/v1", model="gpt-4o-mini",
        )
        helper = await Helper.objects.acreate(
            project=project, name="grader", description="", endpoint_url="https://h.example/v1",
            roles=["judge"],
        )
        await _run(project, used, when=now - timedelta(days=2) + timedelta(hours=1),
                   status="Done", component=component, connection=connection, helper=helper)

        body = (await client.get(f"/{project.pid}/results?version={v1.pid}")).json()
        tool = body["tools"][0]
        self.assertEqual(tool["connections"], [
            {"name": "judge-endpoint", "protocol_name": "openai"}
        ])
        self.assertEqual(tool["helpers"], [{"role": "judge", "model": "gpt-4o-mini"}])

    async def test_target_scopes_to_a_component(self):
        project = await _project()
        _system, v1, _v2 = await _versions(project)
        now = timezone.now()
        used = await _plugin(project, "fairness")
        first = await _component(project)
        second = await _component(project)
        await _run(project, used, when=now - timedelta(days=2) + timedelta(hours=1),
                   status="Done", component=first)
        await _run(project, used, when=now - timedelta(days=2) + timedelta(hours=2),
                   status="Done", component=second)

        scoped = (await client.get(
            f"/{project.pid}/results?version={v1.pid}&target={first.pid}"
        )).json()
        self.assertEqual(scoped["tools"][0]["executed"], 1)
        self.assertEqual(scoped["target"], str(first.pid))

    async def test_unknown_version_is_404(self):
        project = await _project()
        await _versions(project)
        resp = await client.get(
            f"/{project.pid}/results?version=00000000-0000-0000-0000-000000000000"
        )
        self.assertEqual(resp.status_code, 404)

    @override_settings(RESULTS_DASHBOARD_URL="")
    async def test_bridge_closed_without_a_base_url(self):
        project = await _project()
        await _versions(project)
        body = (await client.get(f"/{project.pid}/results")).json()
        self.assertIsNone(body["dashboards_url"])

    @override_settings(RESULTS_DASHBOARD_URL="https://superset.example/dashboard",
                       RESULTS_DASHBOARD_SECRET="")
    async def test_no_link_without_a_key_even_with_a_base_url(self):
        """An unsigned link to a dashboard holding every project's data is not shipped."""
        project = await _project()
        await _versions(project)
        body = (await client.get(f"/{project.pid}/results")).json()
        self.assertIsNone(body["dashboards_url"])
        self.assertFalse(body["dashboard"]["authenticated"])
        self.assertEqual(body["dashboard"]["scope"]["project"], str(project.pid))

    @override_settings(RESULTS_DASHBOARD_URL="https://superset.example/dashboard",
                       RESULTS_DASHBOARD_SECRET="link-key")
    async def test_dashboard_links_carry_project_version_and_tool_scope(self):
        project = await _project()
        _system, v1, _v2 = await _versions(project)
        now = timezone.now()
        used = await _plugin(project, "fairness")
        component = await _component(project)
        await _run(project, used, when=now - timedelta(days=2) + timedelta(hours=1),
                   status="Done", component=component)

        body = (await client.get(
            f"/{project.pid}/results?version={v1.pid}&target={component.pid}"
        )).json()
        self.assertIn(f"project={project.pid}", body["dashboards_url"])
        self.assertIn("version=1", body["dashboards_url"])
        self.assertIn(f"target={component.pid}", body["dashboards_url"])
        self.assertIn("tool=fairness", body["tools"][0]["dashboard_url"])

        # authenticated: the link verifies for exactly this scope
        scope, refusal = dashboard_link.verify_link(
            body["dashboards_url"], project=str(project.pid), key="link-key"
        )
        self.assertIsNone(refusal)
        self.assertEqual(scope["version"], "1")
        self.assertEqual(scope["target"], str(component.pid))
        self.assertIsNone(scope["tool"])
        self.assertTrue(body["dashboard"]["authenticated"])
        tool_scope, tool_refusal = dashboard_link.verify_link(
            body["tools"][0]["dashboard_url"], project=str(project.pid), key="link-key"
        )
        self.assertIsNone(tool_refusal)
        self.assertEqual(tool_scope["tool"], "fairness")

        # ... and for no other project
        other, refusal = dashboard_link.verify_link(
            body["dashboards_url"], project="00000000-0000-0000-0000-000000000000",
            key="link-key",
        )
        self.assertIsNone(other)
        self.assertEqual(refusal, dashboard_link.WRONG_PROJECT)

    @override_settings(RESULTS_DASHBOARD_URL="https://superset.example/dashboard",
                       RESULTS_DASHBOARD_SECRET="link-key")
    async def test_two_projects_never_see_each_other(self):
        first = await _project("isolation-a")
        second = await _project("isolation-b")
        await _versions(first)
        await _versions(second)
        now = timezone.now()
        a_plugin = await _plugin(first, "fairness")
        b_plugin = await _plugin(second, "robustness")
        await _run(first, a_plugin, when=now, status="Done", component=await _component(first))
        await _run(second, b_plugin, when=now, status="Done", component=await _component(second))

        a_body = (await client.get(f"/{first.pid}/results")).json()
        b_body = (await client.get(f"/{second.pid}/results")).json()
        self.assertEqual([t["plugin_name"] for t in a_body["tools"]], ["fairness"])
        self.assertEqual([t["plugin_name"] for t in b_body["tools"]], ["robustness"])

        # a project the caller is not in is not theirs to read
        third = await _project("isolation-c", member=False)
        stranger = await client.get(f"/{third.pid}/results")
        self.assertEqual(stranger.status_code, 404)

        # and a link minted for one project does not verify for the other
        scope, refusal = dashboard_link.verify_link(
            a_body["dashboards_url"], project=str(second.pid), key="link-key"
        )
        self.assertIsNone(scope)
        self.assertEqual(refusal, dashboard_link.WRONG_PROJECT)
        self.assertNotEqual(a_body["dashboard"]["scope"]["project"],
                            b_body["dashboard"]["scope"]["project"])

    @override_settings(RESULTS_DASHBOARD_URL="https://superset.example/dashboard",
                       RESULTS_DASHBOARD_SECRET="link-key")
    async def test_charts_are_the_visualizer_aggregate_over_the_scoped_runs(self):
        """The results page's charts and the visualizer read the same rows."""
        project = await _project("charts")
        _system, v1, _v2 = await _versions(project)
        now = timezone.now()
        used = await _plugin(project, "fairness")
        component = await _component(project)
        evaluation, _run_row = await _run(
            project, used, when=now, status="Done", component=component
        )
        _evaluation, local_row = await _run(
            project, used, when=now, status="Done", component=component, local=True
        )
        await _measurements(evaluation, "accuracy", [0.4, 0.6])
        await _measurements(local_row.evaluation, "accuracy", [0.99])

        body = (await client.get(f"/{project.pid}/results")).json()
        self.assertEqual([c["metric"] for c in body["charts"]], ["accuracy"])
        chart = body["charts"][0]
        self.assertEqual(chart["count"], 2)
        self.assertAlmostEqual(chart["avg_score"], 0.5, places=6)
        self.assertAlmostEqual(chart["min_score"], 0.4, places=6)
        self.assertAlmostEqual(chart["max_score"], 0.6, places=6)
        # the local-model run's observations and measurements are left out
        self.assertEqual(body["tools"][0]["observations"], 1)
        self.assertEqual(body["chart_scope"]["evaluations"], 1)
        self.assertEqual(body["chart_scope"]["local_models_left_out"], 1)

        # the visualizer's own aggregate over the same rows agrees: one row per
        # observation, the assessment one matching the chart and the local one
        # being exactly the figure the chart leaves out
        aggregate = await TestAsyncClient(project_router).post(
            f"/{project.pid}/measurements/aggregate",
            json={"group_by": [], "aggregations": ["count", "min_score", "max_score", "avg_score"]},
        )
        self.assertEqual(aggregate.status_code, 200)
        rows = sorted(aggregate.json()["results"], key=lambda r: r["count"])
        self.assertEqual([r["count"] for r in rows], [1, 2])
        self.assertAlmostEqual(rows[0]["avg_score"], 0.99, places=6)
        self.assertEqual(rows[1]["count"], chart["count"])
        self.assertAlmostEqual(rows[1]["avg_score"], chart["avg_score"], places=6)
        self.assertAlmostEqual(rows[1]["min_score"], chart["min_score"], places=6)
        self.assertAlmostEqual(rows[1]["max_score"], chart["max_score"], places=6)
