"""
Tests for the reporting backend: snapshot assembly, generation against a
stubbed headless service, report read + PDF stream, and project scoping.

The headless report service is stubbed (`report_service.generate` is patched)
so these tests run hermetically (no running service, no network). Auth is the
dev-bypass mode, so `caller_of` returns the development caller (subject
"development").
"""
import io
import json
import unittest.mock as mock

from django.test import TestCase
from ninja.testing import TestAsyncClient
from aisc_backend.models.project import Project
from aisc_backend.models.project_member import MembershipRole, ProjectMember
from aisc_backend.models.report import Report
from aisc_backend.routers.report import router as report_router
from aisc_backend.routers.report import _render_report
from aisc_backend.services import report_service, report_snapshot

report_client = TestAsyncClient(report_router)


async def _project(name="report-project"):
    project = await Project.objects.acreate(name=name, description="", status="Ready")
    await ProjectMember.objects.acreate(
        project=project, subject="development", role=MembershipRole.Owner
    )
    return project


# --- snapshot assembly ----------------------------------------------------------


class SnapshotAssemblyTest(TestCase):

    async def test_empty_project_snapshot(self):
        project = await _project()
        snapshot = await report_snapshot.build_snapshot(project)
        self.assertEqual(snapshot["project"]["pid"], str(project.pid))
        self.assertEqual(snapshot["project"]["name"], "report-project")
        self.assertIsNone(snapshot["system"]["version"])
        self.assertEqual(snapshot["controls"], [])
        self.assertEqual(snapshot["objectives"]["key_objectives"], [])
        self.assertIn("generated_at", snapshot)

    async def test_snapshot_card_carries_plain_graph(self):
        """The report gets the card's graph as plain nodes/edges (not JSON-LD)."""
        project = await _project()
        from aisc_backend.models.qualification import Qualification

        jsonld = json.dumps(
            {
                "@graph": [
                    {"@id": "urn:p", "@type": "AISystem", "label": "Credit Scoring"},
                    {"@id": "urn:c", "@type": "Capability", "label": "Profiling"},
                ]
            }
        )
        await Qualification.objects.acreate(
            project=project,
            system_card={"jsonld": jsonld, "view": {}},
            answers={},
            status="Done",
        )
        snapshot = await report_snapshot.build_snapshot(project)
        card = snapshot["system"]["card"]
        self.assertEqual(card["jsonld"], jsonld)
        self.assertEqual(
            card["graph"],
            {
                "nodes": [
                    {"id": "urn:p", "label": "Credit Scoring", "type": "AISystem"},
                    {"id": "urn:c", "label": "Profiling", "type": "Capability"},
                ],
                "edges": [],
            },
        )

    async def test_snapshot_includes_latest_version_controls_and_objectives(self):
        project = await _project()
        # a card version (the system-versions contract)
        from aisc_backend.models.qualification import System
        from aisc_backend.repositories.system_repository import SystemRepository

        system = await System.objects.acreate(project=project, name="s1")
        version = await SystemRepository().create_version(
            system, name="v1", provider="aisc-qualification"
        )
        self.assertEqual(version.number, 1)

        snapshot = await report_snapshot.build_snapshot(project)
        ref = snapshot["system"]["version"]
        self.assertEqual(ref["number"], 1)
        self.assertEqual(ref["provider"], "aisc-qualification")
        self.assertEqual(snapshot["system"]["name"], "s1")

    async def test_objectives_are_only_the_profiles_selected_ones(self):
        project = await _project()
        # the 0032 seed migration provides the O1..O50 default catalogue; the
        # report keeps the matrix standing of each objective but only lists the
        # ones the project selected in its objective profile
        from aisc_backend.models.control_objective import (
            ControlObjective,
            ObjectiveProfile,
        )

        for objective_id in ("O1", "O2", "O3"):
            await ControlObjective.objects.aupdate_or_create(
                objective_id=objective_id,
                defaults={
                    "macro_requirement": "REQ1 Generic",
                    "text": f"Generic control objective {objective_id}",
                    "assessment_mode": "Control",
                },
            )
        await ObjectiveProfile.objects.acreate(
            project=project, objectives=["O1", "O3"], key_objectives=[]
        )

        snapshot = await report_snapshot.build_snapshot(project)
        objectives = snapshot["objectives"]
        self.assertEqual(
            [p["objective_id"] for p in objectives["priorities"]], ["O1", "O3"]
        )
        self.assertEqual(objectives["key_objectives"], [])
        for p in objectives["priorities"]:
            self.assertIn("rank", p)
            self.assertEqual(p["key"], False)

    async def test_objectives_never_include_another_projects_rows(self):
        project = await _project()
        other = await _project("other-objectives-project")
        from aisc_backend.models.control_objective import (
            ControlObjective,
            ObjectiveProfile,
        )

        await ControlObjective.objects.acreate(
            objective_id="X99",
            project=other,
            macro_requirement="REQ1 Other",
            text="Another project's objective",
            assessment_mode="Control",
        )
        await ObjectiveProfile.objects.acreate(
            project=project, objectives=["O1", "X99"], key_objectives=[]
        )
        snapshot = await report_snapshot.build_snapshot(project)
        ids = [p["objective_id"] for p in snapshot["objectives"]["priorities"]]
        self.assertIn("O1", ids)
        self.assertNotIn("X99", ids)


    async def test_evaluations_carry_declared_visualizations(self):
        project = await _project()
        from aisc_plugin_interface import MetricVisualization

        from aisc_backend.models.evaluation import Evaluation
        from aisc_backend.models.measure import Measurement
        from aisc_backend.models.metric import Metric
        from aisc_backend.models.observation import Observation
        from aisc_backend.models.plugin import EvaluationPlugin, Plugin, PluginConfig

        plugin = await Plugin.objects.acreate(
            project=project,
            package_name="pkg",
            name="Tool",
            version="0.1.0",
            display_name="Tool",
        )
        config = await PluginConfig.objects.acreate(plugin=plugin, config={})
        evaluation = await Evaluation.objects.acreate(project=project, status="Done")
        await EvaluationPlugin.objects.acreate(
            evaluation=evaluation, plugin_config=config
        )
        observation = await Observation.objects.acreate(
            evaluation=evaluation, tool=str(plugin), observer="development"
        )
        metric = await Metric.objects.acreate(name="Accuracy", type_spec="float")
        await Measurement.objects.acreate(
            observation=observation,
            metric=metric,
            score=0.9,
            time=observation.created_at,
        )

        declared = mock.Mock()
        declared.get_metric_visualizations.return_value = [
            MetricVisualization(
                chart_type="line", metrics=["Accuracy"], title="Performance"
            )
        ]
        with mock.patch(
            "aisc_backend.routers.plugin.plugin_loader.load_plugin",
            return_value=declared,
        ):
            snapshot = await report_snapshot.build_snapshot(
                project, evaluation_pids=[str(evaluation.pid)]
            )

        visualizations = snapshot["evaluations"][0]["visualizations"]
        self.assertEqual(len(visualizations), 1)
        self.assertEqual(visualizations[0]["charts"][0]["chart_type"], "line")
        self.assertEqual(visualizations[0]["charts"][0]["title"], "Performance")
        self.assertEqual(visualizations[0]["measurements"][0]["metric"], "Accuracy")
        self.assertEqual(visualizations[0]["measurements"][0]["score"], 0.9)

    async def test_snapshot_collects_dashboard_widgets(self):
        """The dashboard's widgets are kept as references: a chart carries its
        report-owned artifact reference + title + comment, a note its text."""
        project = await _project()
        widgets = [
            {
                "key": "c1",
                "kind": "chart",
                "title": "Accuracy by category",
                "comment": "english results",
                "artifact_id": "00000000-0000-0000-0000-000000000001",
            },
            {"key": "n1", "kind": "text", "text": "a note for the report"},
        ]
        snapshot = await report_snapshot.build_snapshot(
            project, dashboard_widgets=widgets
        )

        collected = snapshot["dashboard"]["widgets"]
        self.assertEqual(len(collected), 2)
        chart, note = collected
        self.assertEqual(chart["kind"], "chart")
        self.assertEqual(chart["title"], "Accuracy by category")
        self.assertEqual(chart["comment"], "english results")
        self.assertEqual(
            chart["image"], "artifact:00000000-0000-0000-0000-000000000001"
        )
        self.assertNotIn("svg", chart)
        self.assertEqual(note["kind"], "text")
        self.assertEqual(note["text"], "a note for the report")

    async def test_dashboard_section_follows_its_widgets(self):
        """Widgets picked must render even for a project whose saved default
        sections predate the "dashboard" chapter."""
        project = await _project()
        snapshot = await report_snapshot.build_snapshot(
            project,
            sections=["system", "controls"],
            dashboard_widgets=[
                {
                    "key": "c1",
                    "kind": "chart",
                    "title": "t",
                    "artifact_id": "00000000-0000-0000-0000-000000000001",
                }
            ],
        )
        self.assertIn("dashboard", snapshot["sections"])
        # without widgets the section list is untouched
        plain = await report_snapshot.build_snapshot(
            project, sections=["system", "controls"]
        )
        self.assertNotIn("dashboard", plain["sections"])

    async def test_snapshot_drops_chart_without_artifact_but_keeps_note(self):
        """A chart with no uploaded artifact reference is left out; a note is
        kept; text is capped."""
        project = await _project()
        snapshot = await report_snapshot.build_snapshot(
            project,
            dashboard_widgets=[
                {"key": "c1", "kind": "chart", "title": "empty"},
                {
                    "key": "c2",
                    "kind": "chart",
                    "title": "has artifact",
                    "artifact_id": "00000000-0000-0000-0000-000000000002",
                },
                {"key": "n1", "kind": "text", "text": "x" * 5000},
            ],
        )
        widgets = snapshot["dashboard"]["widgets"]
        self.assertEqual([w["key"] for w in widgets], ["c2", "n1"])
        self.assertEqual(
            widgets[0]["image"], "artifact:00000000-0000-0000-0000-000000000002"
        )
        self.assertEqual(len(widgets[1]["text"]), 2000)


# --- generation + reads (service stubbed) ---------------------------------------


class ReportEndpointsTest(TestCase):

    async def test_generate_stores_report_and_audits(self):
        project = await _project()
        with mock.patch(
            "aisc_backend.routers.report._launch_worker",
            new=mock.Mock(),
        ) as launch:
            resp = await report_client.post(
                f"/{project.pid}/reports/generate",
                json={"period": "2026-Q3", "format": "pdf"},
            )
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertEqual(body["period"], "2026-Q3")
        self.assertEqual(body["format"], "pdf")
        self.assertEqual(body["version"], 1)
        self.assertEqual(body["status"], "pending")
        self.assertEqual(body["project_id"], str(project.pid))
        self.assertEqual(body["controls"], [])
        self.assertTrue(body["created_at"])

        # the endpoint answers immediately and hands the render to the worker
        launch.assert_called_once()
        _, snapshot, fmt = launch.call_args.args
        self.assertEqual(snapshot["project"]["pid"], str(project.pid))
        self.assertEqual(snapshot["style"]["report_version"], 1)
        self.assertEqual(fmt, "pdf")

        report = await Report.objects.afirst()
        self.assertIsNotNone(report)
        self.assertIsNone(report.document)      # the worker stores it later
        self.assertEqual(report.status, "pending")
        self.assertEqual(report.system_version_id, None)  # no card version yet

    async def test_generate_with_card_version_links_it(self):
        project = await _project()
        from aisc_backend.models.qualification import System
        from aisc_backend.repositories.system_repository import SystemRepository

        system = await System.objects.acreate(project=project, name="s")
        version = await SystemRepository().create_version(system, name="v1")
        with mock.patch(
            "aisc_backend.services.report_service.generate",
            new=mock.AsyncMock(return_value=(b"doc", "text/html")),
        ), mock.patch(
            "aisc_backend.routers.report._launch_worker",
            new=mock.Mock(),
        ):
            resp = await report_client.post(
                f"/{project.pid}/reports/generate",
                json={"period": "", "format": "html"},
            )
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json()["format"], "html")
        report = await Report.objects.afirst()
        self.assertEqual(report.system_version_id, version.pk)
        self.assertEqual(resp.json()["system_version"]["number"], 1)

    async def test_list_and_get_reports(self):
        project = await _project()
        with mock.patch(
            "aisc_backend.services.report_service.generate",
            new=mock.AsyncMock(return_value=(b"%PDF x", "application/pdf")),
        ), mock.patch(
            "aisc_backend.routers.report._launch_worker",
            new=mock.Mock(),
        ):
            created = (
                await report_client.post(
                    f"/{project.pid}/reports/generate",
                    json={"period": "P1", "format": "pdf"},
                )
            ).json()
            await report_client.post(
                f"/{project.pid}/reports/generate",
                json={"period": "P2", "format": "pdf"},
            )

        listing = (await report_client.get(f"/{project.pid}/reports")).json()
        self.assertEqual(len(listing), 2)

        detail = (
            await report_client.get(f"/{project.pid}/reports/{created['pid']}")
        ).json()
        self.assertEqual(detail["pid"], created["pid"])
        self.assertEqual(detail["period"], "P1")

    async def test_pdf_streams_stored_document(self):
        project = await _project()
        fake_doc = b"%PDF-1.4 fake report content"
        with mock.patch(
            "aisc_backend.routers.report._launch_worker",
            new=mock.Mock(),
        ) as launch:
            created = (
                await report_client.post(
                    f"/{project.pid}/reports/generate",
                    json={"period": "", "format": "pdf"},
                )
            ).json()

        # simulate the background worker finalising the pending report
        with mock.patch(
            "aisc_backend.services.report_service.generate",
            new=mock.AsyncMock(return_value=(fake_doc, "application/pdf")),
        ):
            await _render_report(str(created["pid"]), launch.call_args.args[1], "pdf")

        resp = await report_client.get(
            f"/{project.pid}/reports/{created['pid']}/pdf"
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.content, fake_doc)
        self.assertEqual(resp.headers["Content-Type"], "application/pdf")

    async def test_stranger_cannot_generate(self):
        project = await Project.objects.acreate(name="x", description="", status="Ready")
        # no member rows -> the dev caller has no membership
        resp = await report_client.post(
            f"/{project.pid}/reports/generate",
            json={"period": "", "format": "pdf"},
        )
        self.assertEqual(resp.status_code, 404)

    async def test_missing_report_is_404(self):
        project = await _project()
        resp = await report_client.get(
            f"/{project.pid}/reports/00000000-0000-0000-0000-000000000000"
        )
        self.assertEqual(resp.status_code, 404)

    async def test_delete_report_removes_it(self):
        project = await _project()
        with mock.patch(
            "aisc_backend.services.report_service.generate",
            new=mock.AsyncMock(return_value=(b"%PDF x", "application/pdf")),
        ), mock.patch(
            "aisc_backend.routers.report._launch_worker",
            new=mock.Mock(),
        ):
            created = (
                await report_client.post(
                    f"/{project.pid}/reports/generate",
                    json={"period": "P1", "format": "pdf"},
                )
            ).json()

        resp = await report_client.delete(f"/{project.pid}/reports/{created['pid']}")
        self.assertEqual(resp.status_code, 204, resp.text)
        self.assertFalse(await Report.objects.filter(pid=created["pid"]).aexists())

        again = await report_client.get(f"/{project.pid}/reports/{created['pid']}")
        self.assertEqual(again.status_code, 404)

    async def test_delete_report_is_scoped_to_project(self):
        project = await _project()
        other = await _project("other-report-project")
        with mock.patch(
            "aisc_backend.services.report_service.generate",
            new=mock.AsyncMock(return_value=(b"%PDF x", "application/pdf")),
        ), mock.patch(
            "aisc_backend.routers.report._launch_worker",
            new=mock.Mock(),
        ):
            created = (
                await report_client.post(
                    f"/{project.pid}/reports/generate",
                    json={"period": "P1", "format": "pdf"},
                )
            ).json()

        resp = await report_client.delete(f"/{other.pid}/reports/{created['pid']}")
        self.assertEqual(resp.status_code, 404)
        self.assertTrue(await Report.objects.filter(pid=created["pid"]).aexists())

    async def test_generate_passes_customisation_to_snapshot(self):
        project = await _project()
        await report_client.put(
            f"/{project.pid}/report-settings",
            json={
                "report_title": "My report",
                "subtitle": "Q3 assessment",
                "primary_color": "#112233",
                "accent_color": "#445566",
                "font": "dejavu-serif",
                "font_size_pt": 12,
                "header_text": "ACME",
                "marking": "internal",
                "show_document_id": True,
            },
        )
        with mock.patch(
            "aisc_backend.routers.report._launch_worker",
            new=mock.Mock(),
        ) as launch:
            resp = await report_client.post(
                f"/{project.pid}/reports/generate",
                json={"period": "P1", "format": "pdf"},
            )
        self.assertEqual(resp.status_code, 200, resp.text)
        style = launch.call_args.args[1]["style"]
        self.assertEqual(style["report_title"], "My report")
        self.assertEqual(style["subtitle"], "Q3 assessment")
        self.assertEqual(style["primary_color"], "#112233")
        self.assertEqual(style["accent_color"], "#445566")
        self.assertEqual(style["font"], "dejavu-serif")
        self.assertEqual(style["font_size_pt"], 12)
        self.assertEqual(style["header_text"], "ACME")
        self.assertEqual(style["marking"], "internal")
        self.assertTrue(style["show_document_id"])
        # the report's own version (not the AI card's) is part of the style
        self.assertEqual(style["report_version"], 1)
        self.assertEqual(resp.json()["version"], 1)
        # the document id line is part of the style
        self.assertTrue(style["document_id"].startswith("AISC-"))
        self.assertEqual(len(style["fingerprint"]), 12)

    async def test_report_versions_increment_per_project(self):
        project = await _project()
        other = await _project("another-project")
        with mock.patch(
            "aisc_backend.services.report_service.generate",
            new=mock.AsyncMock(return_value=(b"%PDF x", "application/pdf")),
        ), mock.patch(
            "aisc_backend.routers.report._launch_worker",
            new=mock.Mock(),
        ):
            first = (await report_client.post(
                f"/{project.pid}/reports/generate", json={"period": "P1"}
            )).json()
            second = (await report_client.post(
                f"/{project.pid}/reports/generate", json={"period": "P2"}
            )).json()
            # another project starts back at 1: the versions are per project
            other_first = (await report_client.post(
                f"/{other.pid}/reports/generate", json={"period": "P1"}
            )).json()
        self.assertEqual(first["version"], 1)
        self.assertEqual(second["version"], 2)
        self.assertEqual(other_first["version"], 1)

    async def test_deleting_a_mid_version_report_does_not_reuse_its_number(self):
        """Versions are max(version) + 1: deleting report v2 of v1..v3 yields v4,
        never a duplicate v3 (a count-based numbering would collide)."""
        project = await _project()
        with mock.patch(
            "aisc_backend.services.report_service.generate",
            new=mock.AsyncMock(return_value=(b"%PDF x", "application/pdf")),
        ), mock.patch(
            "aisc_backend.routers.report._launch_worker",
            new=mock.Mock(),
        ):
            first = (await report_client.post(
                f"/{project.pid}/reports/generate", json={"period": "P1"}
            )).json()
            second = (await report_client.post(
                f"/{project.pid}/reports/generate", json={"period": "P2"}
            )).json()
            third = (await report_client.post(
                f"/{project.pid}/reports/generate", json={"period": "P3"}
            )).json()
            # drop the middle report, then generate again
            deleted = (await report_client.delete(
                f"/{project.pid}/reports/{second['pid']}"
            ))
            self.assertEqual(deleted.status_code, 204)
            fourth = (await report_client.post(
                f"/{project.pid}/reports/generate", json={"period": "P4"}
            )).json()
        self.assertEqual(
            [first["version"], second["version"], third["version"], fourth["version"]],
            [1, 2, 3, 4],
        )



class ReportSettingsTest(TestCase):

    async def test_settings_missing_then_put_then_get(self):
        project = await _project()
        resp = await report_client.get(f"/{project.pid}/report-settings")
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json()["default_format"], "pdf")
        self.assertEqual(resp.json()["default_sections"], [])
        self.assertEqual(resp.json()["report_title"], "")
        self.assertEqual(resp.json()["primary_color"], "#000fdf")
        self.assertEqual(resp.json()["accent_color"], "#ff007e")

        resp = await report_client.put(
            f"/{project.pid}/report-settings",
            json={
                "default_format": "html",
                "default_sections": ["system", "objectives"],
                "report_title": "Annual assessment",
                "subtitle": "2026",
                "primary_color": "#112233",
                "accent_color": "#AABBCC",
                "font": "roboto",
                "font_size_pt": 13.5,
                "header_text": "ACME internal",
                "footer_text": "Confidential",
                "marking": "confidential",
                "show_document_id": True,
                "logo": {"mime": "image/png", "data_base64": "iVBORw0KGgo="},
            },
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertEqual(body["default_format"], "html")
        self.assertEqual(body["default_sections"], ["system", "objectives"])
        self.assertEqual(body["report_title"], "Annual assessment")
        self.assertEqual(body["subtitle"], "2026")
        self.assertEqual(body["primary_color"], "#112233")
        self.assertEqual(body["accent_color"], "#aabbcc")
        self.assertEqual(body["font"], "roboto")
        self.assertEqual(body["font_size_pt"], 13.5)
        self.assertEqual(body["header_text"], "ACME internal")
        self.assertEqual(body["footer_text"], "Confidential")
        self.assertEqual(body["marking"], "confidential")
        self.assertTrue(body["show_document_id"])
        self.assertEqual(body["logo"]["mime"], "image/png")

        again = (await report_client.get(f"/{project.pid}/report-settings")).json()
        self.assertEqual(again["default_format"], "html")
        self.assertEqual(again["report_title"], "Annual assessment")
        self.assertEqual(again["accent_color"], "#aabbcc")
        self.assertEqual(again["font"], "roboto")
        self.assertTrue(again["show_document_id"])

    async def test_settings_reject_unknown_sections_and_bad_format(self):
        project = await _project()
        resp = await report_client.put(
            f"/{project.pid}/report-settings",
            json={
                "default_format": "docx",
                "default_sections": ["system", "nope"],
                "primary_color": "red; }",
                "accent_color": "#12345",
                "report_title": "x" * 300,
                "font": "comic-sans",
                "font_size_pt": 99,
                "marking": "top-secret",
                "header_text": "y" * 200,
                "logo": {"mime": "image/gif", "data_base64": "x"},
            },
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertEqual(body["default_format"], "pdf")
        self.assertEqual(body["default_sections"], ["system"])
        self.assertEqual(body["primary_color"], "#000fdf")
        self.assertEqual(body["accent_color"], "#ff007e")
        self.assertEqual(len(body["report_title"]), 200)
        self.assertEqual(body["font"], "inter")
        self.assertEqual(body["font_size_pt"], 16.0)  # 99 clamps to the max 16
        self.assertEqual(body["marking"], "none")
        self.assertEqual(len(body["header_text"]), 120)
        self.assertIsNone(body["logo"])

    async def test_stranger_cannot_read_or_write_settings(self):
        project = await Project.objects.acreate(name="s", description="", status="Ready")
        resp = await report_client.get(f"/{project.pid}/report-settings")
        self.assertEqual(resp.status_code, 404)
        resp = await report_client.put(
            f"/{project.pid}/report-settings", json={"default_format": "html"}
        )
        self.assertEqual(resp.status_code, 404)

    async def test_generate_merges_settings_and_scopes_evaluations(self):
        from aisc_backend.models.evaluation import Evaluation

        project = await _project()
        await report_client.put(
            f"/{project.pid}/report-settings",
            json={"default_format": "html", "default_sections": ["objectives"]},
        )
        mine = await Evaluation.objects.acreate(project=project, status="Done")
        other_project = await Project.objects.acreate(name="o", description="", status="Ready")
        foreign = await Evaluation.objects.acreate(project=other_project, status="Done")

        with mock.patch(
            "aisc_backend.routers.report._launch_worker",
            new=mock.Mock(),
        ) as launch:
            resp = await report_client.post(
                f"/{project.pid}/reports/generate",
                json={"period": "2026-Q4", "format": "html",
                      "evaluation_pids": [str(mine.pid)]},
            )
        self.assertEqual(resp.status_code, 200, resp.text)
        pid_arg, snapshot, fmt = launch.call_args.args
        self.assertEqual(fmt, "html")
        self.assertEqual(snapshot["sections"], ["objectives"])
        self.assertEqual([e["pid"] for e in snapshot["evaluations"]], [str(mine.pid)])
        self.assertEqual(pid_arg, str((await Report.objects.afirst()).pid))

        resp = await report_client.post(
            f"/{project.pid}/reports/generate",
            json={"evaluation_pids": [str(foreign.pid)]},
        )
        self.assertEqual(resp.status_code, 404)
        # an invalid evaluation identifier is a validation error, not a 404
        resp = await report_client.post(
            f"/{project.pid}/reports/generate",
            json={"evaluation_pids": ["not-a-uuid"]},
        )
        self.assertEqual(resp.status_code, 422)


class ReportWorkerTest(TestCase):
    """The background renderer (`_render_report`): flips a pending row to
    generated (with the document) or failed."""

    async def _pending_report(self, project) -> tuple[Report, dict]:
        snapshot = {"project": {"pid": str(project.pid)}, "sections": [], "system": {}}
        report = await Report.objects.acreate(
            project=project,
            period="P1",
            version=1,
            format="pdf",
            snapshot=snapshot,
            document=None,
            status="pending",
        )
        return report, snapshot

    async def test_worker_stores_document_and_marks_generated(self):
        project = await _project()
        report, snapshot = await self._pending_report(project)
        fake_doc = b"%PDF-1.4 from worker"
        with mock.patch(
            "aisc_backend.services.report_service.generate",
            new=mock.AsyncMock(return_value=(fake_doc, "application/pdf")),
        ):
            await _render_report(str(report.pid), snapshot, "pdf")
        await report.arefresh_from_db()
        self.assertEqual(report.status, "generated")
        self.assertEqual(bytes(report.document), fake_doc)
        self.assertEqual(report.format, "pdf")
        self.assertTrue(report.file_ref.endswith(".pdf"))

    async def test_worker_keeps_html_format_on_html_content(self):
        project = await _project()
        report, snapshot = await self._pending_report(project)
        with mock.patch(
            "aisc_backend.services.report_service.generate",
            new=mock.AsyncMock(return_value=(b"<html/>", "text/html; charset=utf-8")),
        ):
            await _render_report(str(report.pid), snapshot, "html")
        await report.arefresh_from_db()
        self.assertEqual(report.status, "generated")
        self.assertEqual(report.format, "html")
        self.assertTrue(report.file_ref.endswith(".html"))

    async def test_worker_marks_report_failed_on_error(self):
        project = await _project()
        report, snapshot = await self._pending_report(project)
        with mock.patch(
            "aisc_backend.services.report_service.generate",
            new=mock.AsyncMock(side_effect=report_service.ReportServiceError("down")),
        ):
            await _render_report(str(report.pid), snapshot, "pdf")
        await report.arefresh_from_db()
        self.assertEqual(report.status, "failed")
        self.assertIsNone(report.document)

    async def test_worker_missing_report_is_a_noop(self):
        project = await _project()
        await self._pending_report(project)
        with mock.patch(
            "aisc_backend.services.report_service.generate",
            new=mock.AsyncMock(return_value=(b"%PDF x", "application/pdf")),
        ):
            await _render_report("00000000-0000-0000-0000-000000000000", {}, "pdf")
        # the report does not exist: it must simply not raise


class EvaluationChartExportTest(TestCase):
    def test_exports_are_scoped_to_selected_plugin_declarations(self):
        evaluation = {
            "pid": "selected",
            "visualizations": [{
                "evaluation_plugin_pid": "owned-plugin",
                "charts": [{"chart_type": "bars", "title": "Server title"},
                           {"chart_type": "table"}],
            }],
        }
        exported = {
            "evaluation_pid": "selected", "evaluation_plugin_pid": "owned-plugin",
            "visualization_index": 0,
            "panels": [{"artifact_id": "00000000-0000-0000-0000-000000000001",
                        "label": "en"}],
        }
        report_snapshot._attach_evaluation_charts([evaluation], [exported])
        self.assertEqual(evaluation["plots"], [{
            "title": "Server title", "description": "",
            "panels": [{"image": "artifact:00000000-0000-0000-0000-000000000001",
                        "label": "en"}],
        }])
        # association is positional: a wrong evaluation, plugin or index yields
        # an empty panel list (the renderer shows an unavailable message)
        for key in ("evaluation_pid", "evaluation_plugin_pid", "visualization_index"):
            invalid = {**exported,
                       key: "foreign" if key != "visualization_index" else 99}
            report_snapshot._attach_evaluation_charts([evaluation], [invalid])
            self.assertEqual(evaluation["plots"][0]["panels"], [])
        # a missing export entry also leaves the panels empty
        report_snapshot._attach_evaluation_charts([evaluation], [])
        self.assertEqual(evaluation["plots"][0]["panels"], [])


class ReportStorageTest(TestCase):
    async def test_shared_dashboard_revision_conflicts(self):
        project = await _project()
        url = f"/{project.pid}/dashboard"
        initial = await report_client.get(url)
        self.assertEqual(initial.json()["revision"], 0)
        payload = {"revision": 0,
                   "widgets": [{"key": "note", "kind": "text", "text": "hello"}],
                   "layouts": {}}
        saved = await report_client.put(url, json=payload)
        self.assertEqual(saved.status_code, 200)
        self.assertEqual(saved.json()["revision"], 1)
        conflict = await report_client.put(url, json=payload)
        self.assertEqual(conflict.status_code, 409)
        loaded = await report_client.get(url)
        self.assertEqual(loaded.json()["widgets"], payload["widgets"])

    async def test_artifact_references_finalize_once_and_enforce_ownership(self):
        from aisc_backend.models.report_storage import ReportArtifact
        from aisc_backend.services.report_storage import for_renderer
        project = await _project()
        prepared = await report_client.post(f"/{project.pid}/reports/prepare", json={"period": "Q1"})
        self.assertEqual(prepared.status_code, 200)
        report_pid = prepared.json()["pid"]
        report = await Report.objects.aget(pid=report_pid)
        artifact = await ReportArtifact.objects.acreate(report=report, name="chart.png", description="",
                                                        data="reports/chart.png")
        payload = {"report_pid": report_pid, "dashboard": [
            {"key": "chart", "kind": "chart", "artifact_id": str(artifact.pid)}]}
        with mock.patch("aisc_backend.routers.report._launch_worker") as launch:
            response = await report_client.post(f"/{project.pid}/reports/generate", json=payload)
            self.assertEqual(response.status_code, 200)
            duplicate = await report_client.post(f"/{project.pid}/reports/generate", json=payload)
            self.assertEqual(duplicate.status_code, 409)
            launch.assert_called_once()
        await report.arefresh_from_db()
        image = report.snapshot["dashboard"]["widgets"][0]["image"]
        self.assertEqual(image, f"artifact:{artifact.pid}")
        self.assertNotIn("base64", json.dumps(report.snapshot))
        rendered = for_renderer(report.snapshot, str(project.pid))
        self.assertIn("/report-artifacts/", rendered["dashboard"]["widgets"][0]["image"])
        other = await report_client.post(f"/{project.pid}/reports/prepare", json={})
        payload["report_pid"] = other.json()["pid"]
        denied = await report_client.post(f"/{project.pid}/reports/generate", json=payload)
        self.assertEqual(denied.status_code, 404)

    def test_png_upload_uses_artifact_bucket_and_rejects_invalid_files(self):
        import io
        import struct
        import zlib
        from django.core.files import File
        from ninja.errors import HttpError
        from aisc_backend.services import report_storage
        def chunk(kind, data):
            return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
        png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
        png += chunk(b"IDAT", zlib.compress(b"\x00\x00\x00\x00")) + chunk(b"IEND", b"")
        project = Project.objects.create(name="storage", description="", status="Ready")
        report = Report.objects.create(project=project, status="preparing")
        with mock.patch.object(report_storage.file_repository, "upload_file", return_value=True) as upload:
            artifact = report_storage.upload(report, File(io.BytesIO(png), name="plot.png"))
            self.assertEqual(artifact.report, report)
            self.assertTrue(artifact.data.startswith(f"reports/{report.pid}/"))
            upload.assert_called_once()
        with self.assertRaises(HttpError):
            report_storage.upload(report, File(io.BytesIO(b"invalid"), name="plot.png"))


    async def test_viewers_cannot_save_dashboard(self):
        project = await _project()
        await ProjectMember.objects.filter(project=project).aupdate(role=MembershipRole.Viewer)
        loaded = await report_client.get(f"/{project.pid}/dashboard")
        self.assertFalse(loaded.json()["can_edit"])
        response = await report_client.put(f"/{project.pid}/dashboard", json={
            "revision": 0, "widgets": [], "layouts": {},
        })
        self.assertEqual(response.status_code, 403)

    async def test_artifact_download_requires_service_auth_and_deletion_removes_files(self):
        from aisc_backend.models.report_storage import ReportArtifact
        project = await _project()
        report = await Report.objects.acreate(project=project, status="preparing")
        artifact = await ReportArtifact.objects.acreate(report=report, name="chart.png",
                                                       description="", data="reports/test.png")
        response = await report_client.get(f"/{project.pid}/report-artifacts/{artifact.pid}")
        self.assertEqual(response.status_code, 401)
        with mock.patch("aisc_backend.services.report_storage.file_repository._get_s3_client") as client:
            deleted = await report_client.delete(f"/{project.pid}/reports/{report.pid}")
            self.assertEqual(deleted.status_code, 204)
            client.return_value.delete_object.assert_called_once()
        self.assertFalse(await ReportArtifact.objects.filter(pk=artifact.pk).aexists())


class ReportValidationTest(TestCase):
    """Preparation and generation reject invalid input before touching the DB."""

    async def test_prepare_rejects_overlong_period_and_unsupported_format(self):
        project = await _project()
        long_period = await report_client.post(
            f"/{project.pid}/reports/prepare", json={"period": "x" * 256}
        )
        self.assertEqual(long_period.status_code, 422)
        bad_format = await report_client.post(
            f"/{project.pid}/reports/prepare", json={"period": "Q1", "format": "docx"}
        )
        self.assertEqual(bad_format.status_code, 422)
        # nothing may be created by an invalid preparation
        self.assertFalse(await Report.objects.aexists())

    async def test_generate_rejects_inline_images_svg_and_unknown_fields(self):
        project = await _project()
        url = f"/{project.pid}/reports/generate"
        chart = {"key": "c", "kind": "chart",
                 "artifact_id": "00000000-0000-0000-0000-000000000001"}
        cases = [
            {**chart, "image": "data:image/png;base64,iVBORw0KGgo="},
            {**chart, "svg": "<svg/>"},
            {**chart, "image": "data:image/png;base64,iVBORw0KGgo=", "svg": "<svg/>"},
            {**chart, "unexpected": "x"},
        ]
        for widget in cases:
            resp = await report_client.post(
                url, json={"period": "Q1", "dashboard": [widget]}
            )
            self.assertEqual(resp.status_code, 422, widget)
        # a chart must reference an uploaded artifact
        resp = await report_client.post(
            url,
            json={"period": "Q1",
                  "dashboard": [{"key": "c", "kind": "chart", "title": "no artifact"}]},
        )
        self.assertEqual(resp.status_code, 422)
        self.assertFalse(await Report.objects.aexists())

    async def test_generate_accepts_an_empty_text_note(self):
        from aisc_backend.models.report_storage import ReportArtifact
        project = await _project()
        prepared = await report_client.post(
            f"/{project.pid}/reports/prepare", json={"period": "Q1"})
        report_pid = prepared.json()["pid"]
        report = await Report.objects.aget(pid=report_pid)
        artifact = await ReportArtifact.objects.acreate(
            report=report, name="chart.png", description="", data="reports/c.png")
        with mock.patch("aisc_backend.routers.report._launch_worker") as launch:
            resp = await report_client.post(
                f"/{project.pid}/reports/generate",
                json={
                    "report_pid": report_pid, "period": "Q1",
                    "dashboard": [
                        {"key": "c1", "kind": "chart", "artifact_id": str(artifact.pid)},
                        {"key": "n1", "kind": "text", "text": ""},
                    ],
                },
            )
            self.assertEqual(resp.status_code, 200, resp.text)
            launch.assert_called_once()
        await report.arefresh_from_db()
        notes = [w for w in report.snapshot["dashboard"]["widgets"] if w["kind"] == "text"]
        self.assertEqual([w["text"] for w in notes], [""])

    async def test_artifact_references_enforce_report_ownership_from_any_collection(self):
        """Dashboard charts and evaluation panels must reference artifacts of
        THIS preparing report; a foreign artifact is refused."""
        from aisc_backend.models.report_storage import ReportArtifact
        project = await _project()
        other = await _project("other-report-project")
        prepared = await report_client.post(
            f"/{project.pid}/reports/prepare", json={"period": "Q1"})
        report_pid = prepared.json()["pid"]
        report = await Report.objects.aget(pid=report_pid)
        foreign_report = await Report.objects.acreate(
            project=other, status="preparing", period="Q1")
        foreign = await ReportArtifact.objects.acreate(
            report=foreign_report, name="chart.png", description="", data="reports/x.png")
        foreign_pid = str(foreign.pid)
        # a dashboard chart carrying a foreign artifact is rejected (404 at
        # reference validation) and the report stays preparing
        resp = await report_client.post(
            f"/{project.pid}/reports/generate",
            json={"report_pid": report_pid, "period": "Q1",
                  "dashboard": [{"key": "c", "kind": "chart", "artifact_id": foreign_pid}]},
        )
        self.assertEqual(resp.status_code, 404, resp.text)
        await report.arefresh_from_db()
        self.assertEqual(report.status, "preparing")
        self.assertIsNone(report.document)


class ArtifactCapTest(TestCase):
    """REPORT_MAX_ARTIFACTS is enforced under concurrent uploads; a failed
    upload must release its reservation."""

    def _file(self):
        from django.core.files.uploadedfile import UploadedFile
        uploaded = UploadedFile(
            io.BytesIO(b"\x89PNG\r\n\x1a\n" + b"x" * 32), name="chart.png")
        uploaded.size = 40
        return uploaded

    async def test_cap_rejects_overflow_and_failure_releases_slot(self):
        import uuid as uuid_mod

        from django.test import override_settings

        from aisc_backend.services import report_storage
        project = await _project()
        prepared = await report_client.post(
            f"/{project.pid}/reports/prepare", json={"period": "Q1"})
        report_pid = prepared.json()["pid"]
        report = await Report.objects.aget(pid=report_pid)
        url = f"/{project.pid}/reports/{report_pid}/artifacts"
        artifact = mock.Mock()
        artifact.pid = uuid_mod.uuid4()

        with override_settings(REPORT_MAX_ARTIFACTS=2):
            # a failing upload must not consume a slot
            from ninja.errors import HttpError
            with mock.patch.object(
                report_storage, "upload", new=mock.Mock(side_effect=HttpError(422, "bad png")),
            ):
                resp = await report_client.post(url, FILES={"file": self._file()})
            self.assertEqual(resp.status_code, 422)
            await report.arefresh_from_db()
            self.assertEqual(report.reserved_slots, 0)
            # two successful uploads fill the cap
            with mock.patch.object(report_storage, "upload",
                                   new=mock.Mock(return_value=artifact)):
                for _ in range(2):
                    resp = await report_client.post(url, FILES={"file": self._file()})
                    self.assertEqual(resp.status_code, 200, resp.text)
            await report.arefresh_from_db()
            self.assertEqual(report.reserved_slots, 2)
            # the next upload is refused over capacity
            with mock.patch.object(report_storage, "upload",
                                   new=mock.Mock(return_value=artifact)):
                resp = await report_client.post(url, FILES={"file": self._file()})
            self.assertEqual(resp.status_code, 413)
            await report.arefresh_from_db()
            self.assertEqual(report.reserved_slots, 2)


class DashboardValidationTest(TestCase):
    """The shared dashboard save is schema-checked; malformed data is rejected
    with field-level errors, never truncated."""

    async def _save(self, project, payload):
        return await report_client.put(f"/{project.pid}/dashboard", json=payload)

    async def test_valid_dashboard_round_trips_verbatim(self):
        project = await _project()
        payload = {
            "revision": 0,
            "widgets": [
                {"key": "w1", "kind": "chart", "plugin": "quality",
                 "chartType": "line", "title": "Accuracy", "metrics": ["acc"]},
                {"key": "n1", "kind": "text", "text": "a note"},
            ],
            "layouts": {
                "lg": [{"i": "w1", "x": 0, "y": 0, "w": 6, "h": 6}],
                "md": [{"i": "w1", "x": 0, "y": 0, "w": 5, "h": 6, "minW": 2, "minH": 3}],
            },
        }
        resp = await self._save(project, payload)
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertEqual(body["widgets"], payload["widgets"])
        self.assertEqual(body["layouts"], payload["layouts"])
        self.assertEqual(body["revision"], 1)

    async def test_malformed_widgets_and_geometry_fail_validation(self):
        project = await _project()
        base_widgets = [{"key": "w1", "kind": "chart", "chartType": "line"}]
        bad_payloads = [
            ("unknown widget kind",
             {"widgets": [{"key": "w1", "kind": "bogus"}], "layouts": {}}),
            ("unexpected widget field",
             {"widgets": [{"key": "w1", "kind": "chart", "bogus": 1}], "layouts": {}}),
            ("unknown chart type",
             {"widgets": [{"key": "w1", "kind": "chart", "chartType": "sparkline"}],
              "layouts": {}}),
            ("overlong widget key",
             {"widgets": [{"key": "w" * 257, "kind": "text", "text": "x"}], "layouts": {}}),
            ("overlong title",
             {"widgets": [{"key": "w1", "kind": "chart", "title": "t" * 201,
                           "chartType": "line"}], "layouts": {}}),
            ("duplicate widget keys",
             {"widgets": [{"key": "w1", "kind": "text", "text": "a"},
                          {"key": "w1", "kind": "text", "text": "b"}], "layouts": {}}),
            ("too many widgets",
             {"widgets": [{"key": f"w{i}", "kind": "text", "text": "x"}
                          for i in range(201)], "layouts": {}}),
            ("layout refers to an unknown widget",
             {"widgets": base_widgets,
              "layouts": {"lg": [{"i": "ghost", "x": 0, "y": 0, "w": 6, "h": 6}]}}),
            ("duplicate layout entries",
             {"widgets": base_widgets,
              "layouts": {"lg": [{"i": "w1", "x": 0, "y": 0, "w": 6, "h": 6},
                                 {"i": "w1", "x": 6, "y": 0, "w": 6, "h": 6}]}}),
            ("unknown breakpoint",
             {"widgets": base_widgets,
              "layouts": {"xl": [{"i": "w1", "x": 0, "y": 0, "w": 6, "h": 6}]}}),
            ("widget wider than the breakpoint columns",
             {"widgets": base_widgets,
              "layouts": {"lg": [{"i": "w1", "x": 20, "y": 0, "w": 6, "h": 6}]}}),
            ("negative position",
             {"widgets": base_widgets,
              "layouts": {"lg": [{"i": "w1", "x": -1, "y": 0, "w": 6, "h": 6}]}}),
            ("zero dimension",
             {"widgets": base_widgets,
              "layouts": {"lg": [{"i": "w1", "x": 0, "y": 0, "w": 0, "h": 6}]}}),
        ]
        for name, payload in bad_payloads:
            resp = await self._save(project, payload)
            self.assertEqual(resp.status_code, 422, name)
        # nothing was saved by any rejected payload
        loaded = (await report_client.get(f"/{project.pid}/dashboard")).json()
        self.assertEqual(loaded["widgets"], [])
        self.assertEqual(loaded["revision"], 0)


    async def test_scalar_filter_values_are_accepted(self):
        """Imported plugin filters may carry numbers/booleans; the save must
        keep them rather than reject the dashboard."""
        project = await _project()
        payload = {
            "revision": 0,
            "widgets": [{
                "key": "w1", "kind": "chart", "chartType": "line",
                "metrics": ["acc"],
                "filters": {"threshold": [0.5, 1, True, False]},
            }],
            "layouts": {},
        }
        resp = await self._save(project, payload)
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(
            resp.json()["widgets"][0]["filters"]["threshold"], [0.5, 1, True, False]
        )

    def test_report_max_artifacts_is_environment_backed(self):
        """Check both an override and the default without reloading shared settings
        or depending on the test runner's environment / local .env file."""
        import os
        from pathlib import Path
        import subprocess
        import sys

        script = (
            "from environs import env; "
            "env.read_env = lambda *args, **kwargs: None; "
            "from config.settings import REPORT_MAX_ARTIFACTS; "
            "print(REPORT_MAX_ARTIFACTS)"
        )
        for configured, expected in (("7", "7"), (None, "100")):
            with self.subTest(configured=configured):
                child_env = os.environ.copy()
                child_env.pop("REPORT_MAX_ARTIFACTS", None)
                if configured is not None:
                    child_env["REPORT_MAX_ARTIFACTS"] = configured
                result = subprocess.run(
                    [sys.executable, "-c", script], env=child_env,
                    cwd=Path(__file__).resolve().parents[3],
                    check=True, capture_output=True, text=True,
                )
                self.assertEqual(result.stdout.strip(), expected)

    def test_metadata_failure_removes_uploaded_object(self):
        import struct
        import zlib

        from django.core.files.base import File
        from django.db import IntegrityError

        from aisc_backend.models.report_storage import ReportArtifact
        from aisc_backend.services import report_storage

        def chunk(kind, data):
            return (struct.pack(">I", len(data)) + kind + data
                    + struct.pack(">I", zlib.crc32(kind + data)))

        raw = (b"\x89PNG\r\n\x1a\n"
               + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0))
               + chunk(b"IDAT", zlib.compress(b"\x00\xff\xff\xff\xff"))
               + chunk(b"IEND", b""))
        project = Project.objects.create(name="upload", status="Ready")
        report = Report.objects.create(project=project, status="preparing")

        def delete_during_upload(*args):
            Report.objects.filter(pk=report.pk).delete()
            return True

        with mock.patch.object(report_storage.file_repository, "upload_file",
                               side_effect=delete_during_upload) as upload:
            for cleanup in (True, False, RuntimeError("storage down")):
                with self.subTest(cleanup=cleanup):
                    kwargs = ({"side_effect": cleanup} if isinstance(cleanup, Exception)
                              else {"return_value": cleanup})
                    with mock.patch.object(report_storage.file_repository,
                                           "delete_object", **kwargs) as delete:
                        with (
                            mock.patch.object(ReportArtifact.objects, "create",
                                              side_effect=IntegrityError("report deleted")),
                            self.assertRaisesRegex(IntegrityError, "report deleted"),
                        ):
                            report_storage.upload(report, File(io.BytesIO(raw), name="plot.png"))
                        delete.assert_called_once_with(upload.call_args.args[1],
                                                       upload.call_args.args[2])
        self.assertFalse(ReportArtifact.objects.filter(report_id=report.pk).exists())

    def test_delete_files_continues_after_a_storage_exception(self):
        from aisc_backend.models.report_storage import ReportArtifact
        from aisc_backend.services import report_storage
        project = Project.objects.create(name="del", description="", status="Ready")
        report = Report.objects.create(project=project, status="preparing")
        failed = ReportArtifact.objects.create(
            report=report, name="1.png", description="", data="reports/a.png")
        deleted_ok = ReportArtifact.objects.create(
            report=report, name="2.png", description="", data="reports/b.png")
        with mock.patch.object(
            report_storage.file_repository, "delete_object",
            side_effect=[RuntimeError("connection reset"), True],
        ) as delete:
            with self.assertRaises(RuntimeError):
                report_storage.delete_files(report)
            # the raising call must not stop the second artifact's deletion
            self.assertEqual(delete.call_count, 2)
        # metadata is removed only for the successfully deleted artifact
        self.assertFalse(ReportArtifact.objects.filter(pk=deleted_ok.pk).exists())
        self.assertTrue(ReportArtifact.objects.filter(pk=failed.pk).exists())


class CleanupReportPreparationsTest(TestCase):
    """The cleanup command removes only abandoned preparations and stale
    pending reports, never fresh/generated ones, and retries kept failures."""

    async def _report(self, project, *, status, created_at, generation_started_at=None):
        report = await Report.objects.acreate(
            project=project, status=status, period="P",
            snapshot={}, generation_started_at=generation_started_at,
        )
        await Report.objects.filter(pk=report.pk).aupdate(created_at=created_at)
        return report

    async def test_cleans_abandoned_preparations_and_stale_pending_only(self):
        from datetime import timedelta

        from django.core.management import call_command
        from django.utils import timezone
        from asgiref.sync import sync_to_async

        project = await _project()
        now = timezone.now()
        old = now - timedelta(hours=25)
        fresh = now - timedelta(hours=1)
        stale_preparing = await self._report(
            project, status="preparing", created_at=old)
        fresh_preparing = await self._report(
            project, status="preparing", created_at=fresh)
        stale_pending = await self._report(
            project, status="pending", created_at=old, generation_started_at=old)
        legacy_pending = await self._report(
            project, status="pending", created_at=old, generation_started_at=None)
        fresh_pending = await self._report(
            project, status="pending", created_at=fresh, generation_started_at=fresh)
        generated = await self._report(
            project, status="generated", created_at=old)
        await sync_to_async(call_command)("cleanup_report_preparations")

        gone = {stale_preparing.pid, stale_pending.pid, legacy_pending.pid}
        kept = {fresh_preparing.pid, fresh_pending.pid, generated.pid}
        present = {
            str(pid) async for pid in
            Report.objects.values_list("pid", flat=True)
        }
        self.assertFalse(
            {str(pid) for pid in gone}.intersection(present),
        )
        self.assertTrue(all(str(pid) in present for pid in kept))

    async def test_cleanup_continues_after_one_failure_and_retries_it(self):
        from datetime import timedelta

        from django.core.management import call_command
        from django.utils import timezone

        project = await _project()
        now = timezone.now()
        old = now - timedelta(hours=25)
        keep_me = await self._report(project, status="preparing", created_at=old)
        wipe_me = await self._report(project, status="preparing", created_at=old)
        from aisc_backend.management.commands import cleanup_report_preparations as cmd

        with mock.patch.object(
            cmd, "delete_files", new=mock.Mock(side_effect=[RuntimeError("s3 down"), None]),
        ) as delete:
            from asgiref.sync import sync_to_async
            await sync_to_async(call_command)("cleanup_report_preparations")
            self.assertEqual(delete.call_count, 2)
        # the deleted artifacts stopped that report's cleanup; it is kept for
        # the next run, while the other report was cleaned up
        self.assertTrue(await Report.objects.filter(pid=keep_me.pid).aexists())
        self.assertFalse(await Report.objects.filter(pid=wipe_me.pid).aexists())


class AuditEventsTest(TestCase):
    async def test_dashboard_save_and_artifact_upload_are_audited(self):
        from django.core.files.uploadedfile import UploadedFile

        from aisc_backend.routers import report as report_module
        project = await _project()
        prepared = await report_client.post(
            f"/{project.pid}/reports/prepare", json={"period": "Q1"})
        report_pid = prepared.json()["pid"]
        with mock.patch.object(report_module, "log_action") as log:
            saved = await report_client.put(f"/{project.pid}/dashboard", json={
                "revision": 0, "widgets": [], "layouts": {}})
            self.assertEqual(saved.status_code, 200)
            artifact = mock.Mock()
            artifact.pid = "00000000-0000-0000-0000-000000000099"
            uploaded = UploadedFile(
                io.BytesIO(b"\x89PNG\r\n\x1a\n" + b"y" * 32), name="chart.png")
            uploaded.size = 40
            with mock.patch.object(report_module.report_storage, "upload",
                                   new=mock.Mock(return_value=artifact)):
                up = await report_client.post(
                    f"/{project.pid}/reports/{report_pid}/artifacts",
                    FILES={"file": uploaded})
                self.assertEqual(up.status_code, 200, up.text)
        calls = {(c.kwargs.get("action"), c.kwargs.get("resource_type"))
                 for c in log.call_args_list}
        self.assertIn(("update", "dashboard"), calls)
        self.assertIn(("upload", "report_artifact"), calls)
        # the upload metadata records identifiers only, never image bytes
        upload_call = next(c for c in log.call_args_list
                           if c.kwargs.get("action") == "upload")
        metadata = upload_call.kwargs.get("metadata", {})
        self.assertIn("projectPid", metadata)
        self.assertIn("report", metadata)
        self.assertNotIn("file", metadata)
        self.assertNotIn("data", metadata)


class ServiceContractTest(TestCase):
    """Backend-generated artifact URLs and the renderer's authentication
    header must agree (the allowlist checks netloc; the door checks the token)."""

    async def test_artifact_urls_and_service_token_header_agree(self):
        from urllib.parse import urlsplit

        from django.conf import settings

        from aisc_backend.services import report_service, report_storage
        token = "s3rv1ce-t0k3n"
        with mock.patch.object(settings, "SERVICE_TOKEN_FOR_APPS", token), \
             mock.patch.object(report_service, "SERVICE_TOKEN_FOR_APPS", token):
            headers = report_service._service_headers()
            self.assertEqual(headers["X-AISC-Service-Token"], token)
            snapshot = {"dashboard": {"widgets": [
                {"key": "c", "image": "artifact:00000000-0000-0000-0000-000000000001"}]}}
            url = report_storage.for_renderer(snapshot, "proj")[
                "dashboard"]["widgets"][0]["image"]
            expected = urlsplit(settings.REPORT_BACKEND_INTERNAL_URL)
            parsed = urlsplit(url)
            self.assertEqual((parsed.scheme, parsed.netloc),
                             (expected.scheme, expected.netloc))
            self.assertIn("/report-artifacts/", parsed.path)
