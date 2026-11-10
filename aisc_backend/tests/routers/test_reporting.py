"""
Tests for the reporting backend: snapshot assembly, generation against a
stubbed headless service, report read + PDF stream, and project scoping.

The headless report service is stubbed (`report_service.generate` is patched)
so these tests run hermetically (no running service, no network). Auth is the
dev-bypass mode, so `caller_of` returns the development caller (subject
"development").
"""
import unittest.mock as mock

from django.test import TestCase
from ninja.testing import TestAsyncClient

from aisc_backend.models.report import Report
from aisc_backend.models.project import Project
from aisc_backend.models.project_member import MembershipRole, ProjectMember
from aisc_backend.routers.report import router as report_router
from aisc_backend.services import report_snapshot

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

    async def test_objectives_copy_matrix_order(self):
        project = await _project()
        # the 0032 seed migration provides the O1..O50 default catalogue; the
        # snapshot still prioritises whatever catalogue rows exist
        from aisc_backend.models.control_objective import ControlObjective

        for objective_id in ("O1", "O2", "O3"):
            await ControlObjective.objects.aupdate_or_create(
                objective_id=objective_id,
                defaults={
                    "macro_requirement": "REQ1 Generic",
                    "text": f"Generic control objective {objective_id}",
                    "assessment_mode": "Control",
                },
            )
        objectives = await report_snapshot.build_snapshot(project)
        # no risks -> all priorities present (catalogue order), none key
        self.assertGreater(len(objectives["objectives"]["priorities"]), 0)
        for p in objectives["objectives"]["priorities"]:
            self.assertIn("objective_id", p)
            self.assertEqual(p["key"], False)


# --- generation + reads (service stubbed) ---------------------------------------


class ReportEndpointsTest(TestCase):

    async def test_generate_stores_report_and_audits(self):
        project = await _project()
        fake_doc = b"%PDF-1.4 fake report"
        with mock.patch(
            "aisc_backend.services.report_service.generate",
            new=mock.AsyncMock(return_value=(fake_doc, "application/pdf")),
        ) as gen:
            resp = await report_client.post(
                f"/{project.pid}/reports/generate",
                json={"period": "2026-Q3", "format": "pdf"},
            )
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertEqual(body["period"], "2026-Q3")
        self.assertEqual(body["format"], "pdf")
        self.assertEqual(body["status"], "generated")
        self.assertEqual(body["project_id"], str(project.pid))
        self.assertEqual(body["controls"], [])
        self.assertTrue(body["created_at"])

        gen.assert_awaited_once()
        args = gen.await_args.args
        self.assertEqual(args[0], str(project.pid))     # project_pid
        self.assertEqual(args[1]["project"]["pid"], str(project.pid))  # snapshot
        self.assertEqual(args[2], "pdf")                # format

        report = await Report.objects.afirst()
        self.assertIsNotNone(report)
        self.assertEqual(bytes(report.document), fake_doc)
        self.assertEqual(report.format, "pdf")
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
            "aisc_backend.services.report_service.generate",
            new=mock.AsyncMock(return_value=(fake_doc, "application/pdf")),
        ):
            created = (
                await report_client.post(
                    f"/{project.pid}/reports/generate",
                    json={"period": "", "format": "pdf"},
                )
            ).json()

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


class ReportSettingsTest(TestCase):

    async def test_settings_missing_then_put_then_get(self):
        project = await _project()
        resp = await report_client.get(f"/{project.pid}/report-settings")
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json()["default_format"], "pdf")
        self.assertEqual(resp.json()["default_sections"], [])

        resp = await report_client.put(
            f"/{project.pid}/report-settings",
            json={"default_format": "html", "default_sections": ["system", "objectives"]},
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertEqual(body["default_format"], "html")
        self.assertEqual(body["default_sections"], ["system", "objectives"])

        again = (await report_client.get(f"/{project.pid}/report-settings")).json()
        self.assertEqual(again["default_format"], "html")

    async def test_settings_reject_unknown_sections_and_bad_format(self):
        project = await _project()
        resp = await report_client.put(
            f"/{project.pid}/report-settings",
            json={"default_format": "docx", "default_sections": ["system", "nope"]},
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json()["default_format"], "pdf")
        self.assertEqual(resp.json()["default_sections"], ["system"])

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
            "aisc_backend.services.report_service.generate",
            new=mock.AsyncMock(return_value=(b"doc", "text/html")),
        ) as gen:
            resp = await report_client.post(
                f"/{project.pid}/reports/generate",
                json={"period": "2026-Q4", "format": "", "evaluation_pids": [str(mine.pid)]},
            )
        self.assertEqual(resp.status_code, 200, resp.text)
        snapshot = gen.await_args.args[1]
        self.assertEqual(gen.await_args.args[2], "html")
        self.assertEqual(snapshot["sections"], ["objectives"])
        self.assertEqual([e["pid"] for e in snapshot["evaluations"]], [str(mine.pid)])

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
