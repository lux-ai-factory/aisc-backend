"""
Tests for the reporting backend: snapshot assembly, generation against a
stubbed headless service, report read + PDF stream, and project scoping.

The headless report service is stubbed (`report_service.generate` is patched)
so these tests run hermetically (no running service, no network). Auth is the
dev-bypass mode, so `caller_of` returns the development caller (subject
"development").
"""
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
                json={"period": "2026-Q4", "format": "", "evaluation_pids": [str(mine.pid)]},
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
        resp = await report_client.post(
            f"/{project.pid}/reports/generate",
            json={"evaluation_pids": ["not-a-uuid"]},
        )
        self.assertEqual(resp.status_code, 404)


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
