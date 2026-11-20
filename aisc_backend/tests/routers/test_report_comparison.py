"""
REP-01 comparison-version rendering/validation, and the local-model run
exclusion ported from the pilot.

These run hermetically (no report service, no network). Auth is the dev-bypass
mode, so the caller subject is "development".
"""
from datetime import timezone

from django.test import TestCase
from django.utils import timezone as dj_timezone
from ninja.testing import TestAsyncClient

from aisc_backend.models.ai_system import AIComponent, AISystem
from aisc_control_objectives.models import ControlObjective
from aisc_evaluation.models import Evaluation
from aisc_control_objectives.models import EvidenceLink
from aisc_evaluation.models import Measurement
from aisc_evaluation.models import Metric
from aisc_evaluation.models import Observation
from aisc_evaluation.models import EvaluationInput, EvaluationPlugin
from aisc_plugins.models import Plugin, PluginConfig
from aisc_backend.models.project import Project
from aisc_backend.models.project_member import MembershipRole, ProjectMember
from aisc_backend.models.system import System
from aisc_backend.repositories.system_repository import SystemRepository
from aisc_backend.routers.report import router as report_router
from aisc_backend.services import report_snapshot

report_client = TestAsyncClient(report_router)


async def _project(name="cmp-project"):
    project = await Project.objects.acreate(name=name, description="", status="Ready")
    await ProjectMember.objects.acreate(
        project=project, subject="development", role=MembershipRole.Owner
    )
    return project


async def _two_versions(project):
    system = await System.objects.acreate(project=project, name="system")
    repo = SystemRepository()
    v1 = await repo.create_version(system, name="v1", provider="aisc")
    v2 = await repo.create_version(system, name="v2", provider="aisc")
    return v1, v2


async def _objective(objective_id):
    objective, _ = await ControlObjective.objects.aupdate_or_create(
        objective_id=objective_id,
        defaults={"macro_requirement": "REQ1 Human Agency and Oversight",
                  "text": f"Objective {objective_id}",
                  "assessment_mode": "Control"},
    )
    return objective


async def _component(project):
    system, _ = await AISystem.objects.aget_or_create(
        project=project, defaults={"name": "assessed system", "description": ""}
    )
    return await AIComponent.objects.acreate(
        system=system, name="the model", description="", data="s3://model",
        component_type="model",
    )


async def _run(project, plugin_name, *, local, component, score, tool=None, when=None):
    """One evaluation with one run; returns the evaluation."""
    plugin = await Plugin.objects.acreate(
        project=project, name=plugin_name, package_name=f"aisc-plugin-{plugin_name}",
        version="1.0", display_name=plugin_name, description="",
    )
    config = await PluginConfig.objects.acreate(plugin=plugin, name="c", description="", config={})
    evaluation = await Evaluation.objects.acreate(project=project, status="Done")
    evaluation_plugin = await EvaluationPlugin.objects.acreate(
        evaluation=evaluation, plugin_config=config, status="Done"
    )
    await EvaluationInput.objects.acreate(
        evaluation_plugin=evaluation_plugin, component=component, name="target", description="",
        value={"local_model": {"path": "/models/local"}} if local else {"target": "card"},
    )
    metric = await Metric.objects.acreate(name="score", description="", type_spec="Direct")
    observation = await Observation.objects.acreate(
        observer="AISC System", tool=tool or str(plugin), evaluation=evaluation
    )
    measurement = await Measurement.objects.acreate(
        observation=observation, metric=metric, name="score", description="",
        score=score, time=when or dj_timezone.now(),
    )
    if when is not None:
        # created_at is auto_now_add; order the observations by hand for the test
        await Observation.objects.filter(pk=observation.pk).aupdate(created_at=when)
    return evaluation


# --- comparison-version (REP-01) ------------------------------------------------


class ComparisonValidationTest(TestCase):

    async def test_compare_to_must_be_an_earlier_version_of_the_project(self):
        project = await _project()
        v1, v2 = await _two_versions(project)
        # the pinned version itself
        with self.assertRaises(report_snapshot.InvalidComparison):
            await report_snapshot.build_snapshot(
                project, version_pid=str(v2.pid), compare_to=str(v2.pid)
            )
        # a newer version than the pin
        with self.assertRaises(report_snapshot.InvalidComparison):
            await report_snapshot.build_snapshot(
                project, version_pid=str(v1.pid), compare_to=str(v2.pid)
            )
        # a version that is not this project's
        with self.assertRaises(report_snapshot.InvalidComparison):
            await report_snapshot.build_snapshot(
                project, version_pid=str(v2.pid),
                compare_to="00000000-0000-0000-0000-000000000000",
            )
        # an earlier version is accepted, and the snapshot records it
        snapshot = await report_snapshot.build_snapshot(
            project, version_pid=str(v2.pid), compare_to=str(v1.pid)
        )
        self.assertIsNotNone(snapshot["comparison"])
        self.assertEqual(snapshot["comparison"]["version"]["pid"], str(v1.pid))

    async def test_no_compare_to_means_no_comparison(self):
        project = await _project()
        v1, v2 = await _two_versions(project)
        snapshot = await report_snapshot.build_snapshot(project, version_pid=str(v2.pid))
        self.assertIsNone(snapshot["comparison"])

    async def test_generate_refuses_a_bad_compare_to(self):
        project = await _project()
        v1, v2 = await _two_versions(project)
        response = await report_client.post(
            f"/{project.pid}/reports/generate",
            json={"version_pid": str(v1.pid), "compare_to": str(v2.pid)},
        )
        self.assertEqual(response.status_code, 422)


class ComparisonDiffTest(TestCase):

    async def test_comparison_reports_gained_lost_and_changed(self):
        project = await _project()
        v1, v2 = await _two_versions(project)
        o1 = await _objective("O1")
        o2 = await _objective("O2")
        o3 = await _objective("O3")
        # v1: O1 covered by old-plugin, O3 covered
        await EvidenceLink.objects.acreate(
            project=project, version=v1, objective=o1, kind="test", key="old-plugin"
        )
        await EvidenceLink.objects.acreate(
            project=project, version=v1, objective=o3, kind="test", key="gone"
        )
        # v2: O1 covered by new-plugin, O2 newly covered, O3 dropped
        await EvidenceLink.objects.acreate(
            project=project, version=v2, objective=o1, kind="test", key="new-plugin"
        )
        await EvidenceLink.objects.acreate(
            project=project, version=v2, objective=o2, kind="test", key="fresh"
        )
        snapshot = await report_snapshot.build_snapshot(
            project, version_pid=str(v2.pid), compare_to=str(v1.pid)
        )
        comparison = snapshot["comparison"]
        self.assertEqual([g["objective_id"] for g in comparison["gained"]], ["O2"])
        self.assertEqual([l["objective_id"] for l in comparison["lost"]], ["O3"])
        changed = {c["objective_id"]: c for c in comparison["changed"]}
        self.assertIn("O1", changed)
        self.assertEqual(changed["O1"]["added"], ["new-plugin"])
        self.assertEqual(changed["O1"]["removed"], ["old-plugin"])
        self.assertEqual(
            comparison["counts"], {"gained": 1, "lost": 1, "changed": 1}
        )


# --- local-model run exclusion (pilot parity) -----------------------------------


class LocalModelExclusionTest(TestCase):

    async def test_local_only_evaluation_is_left_out_of_the_snapshot(self):
        project = await _project()
        component = await _component(project)
        evaluation = await _run(
            project, "LocalTool", local=True, component=component, score=0.1
        )
        snapshot = await report_snapshot.build_snapshot(
            project, evaluation_pids=[str(evaluation.pid)]
        )
        self.assertEqual(snapshot["evaluations"], [])

    async def test_local_run_observations_are_excluded_beside_a_card_run(self):
        project = await _project()
        component = await _component(project)
        early = dj_timezone.datetime(2020, 1, 1, tzinfo=timezone.utc)
        late = dj_timezone.datetime(2021, 1, 1, tzinfo=timezone.utc)
        # one evaluation, two runs: a local one and a card one
        plugin_local = await Plugin.objects.acreate(
            project=project, name="LocTool", package_name="aisc-plugin-loc", version="1",
            display_name="LocTool", description="",
        )
        plugin_card = await Plugin.objects.acreate(
            project=project, name="CardTool", package_name="aisc-plugin-card", version="1",
            display_name="CardTool", description="",
        )
        evaluation = await Evaluation.objects.acreate(project=project, status="Done")
        for plugin, local, score, when in (
            (plugin_local, True, 0.1, early),
            (plugin_card, False, 0.9, late),
        ):
            config = await PluginConfig.objects.acreate(
                plugin=plugin, name="c", description="", config={}
            )
            run = await EvaluationPlugin.objects.acreate(
                evaluation=evaluation, plugin_config=config, status="Done"
            )
            await EvaluationInput.objects.acreate(
                evaluation_plugin=run, component=component, name="target", description="",
                value={"local_model": {"path": "/m"}} if local else {"target": "card"},
            )
            metric = await Metric.objects.acreate(name="score", description="", type_spec="Direct")
            observation = await Observation.objects.acreate(
                observer="AISC System", tool=str(plugin), evaluation=evaluation
            )
            await Measurement.objects.acreate(
                observation=observation, metric=metric, name="score", description="",
                score=score, time=when,
            )
            await Observation.objects.filter(pk=observation.pk).aupdate(created_at=when)

        snapshot = await report_snapshot.build_snapshot(
            project, evaluation_pids=[str(evaluation.pid)]
        )
        self.assertEqual(len(snapshot["evaluations"]), 1)
        key = snapshot["evaluations"][0]["key_results"]
        # the local run's 0.1 is not in the record; the card run's 0.9 is
        self.assertEqual(key["mean_score"], 0.9)
        self.assertEqual(key["observations"], 1)

    async def test_generate_refuses_a_local_only_evaluation(self):
        project = await _project()
        component = await _component(project)
        evaluation = await _run(
            project, "LocalTool", local=True, component=component, score=0.1
        )
        response = await report_client.post(
            f"/{project.pid}/reports/generate",
            json={"evaluation_pids": [str(evaluation.pid)]},
        )
        self.assertEqual(response.status_code, 422)
