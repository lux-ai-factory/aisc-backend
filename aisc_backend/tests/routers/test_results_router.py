"""
Router tests for scoped results and the dashboard bridge (ANA-01).

A stranger gets 404; the selected AI-card version scopes the run counts; a
locally served model's run stays distinct; the dashboard links are built only
when a base URL is configured, and they carry the project/version/tool scope.
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
from aisc_backend.routers.results import router as results_router

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

    @override_settings(RESULTS_DASHBOARD_URL="https://superset.example/dashboard")
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
