"""
REP-01 / REP-02 backend tests: report layouts and blocks, the immutable ORM
snapshot, version selection, and real PDF/Word rendering.

These run hermetically: generation goes through the one in-process renderer
(ADR 0008) and the stored document is a real PDF or DOCX.
Auth is the dev-bypass mode (caller subject "development").
"""
from unittest import mock

from django.test import TestCase
from ninja.testing import TestAsyncClient

from aisc_control_objectives.models import ControlObjective
from aisc_control_objectives.models import EvidenceLink
from aisc_backend.models.project import Project
from aisc_backend.models.project_member import MembershipRole, ProjectMember
from aisc_backend.models.system import System
from aisc_report.models import Report, ReportLayout
from aisc_backend.repositories.system_repository import SystemRepository
from aisc_backend.routers.report import _render_report, router as report_router
from aisc_backend.report_renderer import document as renderer_document
from aisc_backend.services import report_blocks, report_render

report_client = TestAsyncClient(report_router)


async def _project(name="layout-project", role=MembershipRole.Owner):
    project = await Project.objects.acreate(name=name, description="", status="Ready")
    await ProjectMember.objects.acreate(project=project, subject="development", role=role)
    return project


async def _versioned_project(name="versioned"):
    project = await _project(name)
    system = await System.objects.acreate(project=project, name="system")
    repo = SystemRepository()
    v1 = await repo.create_version(system, name="v1", provider="aisc")
    v2 = await repo.create_version(system, name="v2", provider="aisc")
    return project, v1, v2


def _blocks(*type_ids):
    return [report_blocks.new_block(t) for t in type_ids]


async def _generate(project_pid, payload):
    """POST a report with the background worker suppressed, then run the render
    inline so the test observes a finished document. Production renders in a
    thread (``_launch_worker``); tests drive it deterministically instead."""
    with mock.patch("aisc_backend.routers.report._launch_worker", new=mock.Mock()) as launch:
        resp = await report_client.post(
            f"/{project_pid}/reports/generate", json=payload
        )
        if resp.status_code == 200:
            _, snapshot, fmt = launch.call_args.args
            await _render_report(str(resp.json()["pid"]), snapshot, fmt)
    return resp


# --- blocks + built-in layouts --------------------------------------------------


class ReportBlocksTest(TestCase):
    def test_palette_groups_and_builtins(self):
        groups = report_blocks.palette()
        names = [g["group"] for g in groups]
        self.assertIn("Document", names)
        self.assertIn("Summary", names)
        self.assertEqual(len(report_blocks.builtin_layouts()), 5)
        self.assertEqual(
            [b["id"] for b in report_blocks.builtin_layouts()],
            [
                "builtin-summary",
                "builtin-management-overview",
                "builtin-assessment-report",
                "builtin-eu-ai-act",
                "builtin-technical-dossier",
            ],
        )

    def test_builtin_instance_ids_are_stable(self):
        first = report_blocks.builtin_layout("builtin-summary")["blocks"]
        second = report_blocks.builtin_layout("builtin-summary")["blocks"]
        self.assertEqual([b["instance_id"] for b in first], [b["instance_id"] for b in second])

    def test_validate_rejects_unknown_type_duplicate_and_bad_enum(self):
        good = report_blocks.new_block("chapter")
        self.assertEqual(report_blocks.validate_blocks([good]), [])
        unknown = [{"instance_id": good["instance_id"], "block_type": "nope", "options": {}}]
        self.assertEqual(report_blocks.validate_blocks(unknown)[0]["code"], "unknown_block_type")
        dup = [good, dict(good)]
        self.assertEqual(report_blocks.validate_blocks(dup)[0]["code"], "duplicate_instance_id")
        bad_enum = report_blocks.new_block("chart")
        bad_enum["options"] = {"dataset": "not-a-dataset"}
        self.assertEqual(report_blocks.validate_blocks([bad_enum])[0]["code"], "invalid_options")

    def test_outline_numbers_chapters(self):
        blocks = _blocks("cover", "chapter", "control_objectives", "chapter", "test_results", "appendix")
        rows = report_blocks.outline(blocks)
        numbers = [r["number"] for r in rows]
        self.assertEqual(numbers, ["", "1", "", "2", "", "A"])


# --- the renderer ---------------------------------------------------------------


class ReportRenderTest(TestCase):
    def _snapshot(self):
        return {
            "project": {"pid": "p", "name": "Demo", "description": "A system"},
            "system": {"name": "Demo", "version": {"number": 1, "provider": "aisc"}, "card": None},
            "controls": [{"name": "ISO 42001", "status": "Closed", "coverage": "3/3",
                          "readiness": 80, "answers": [{"order": 1, "text": "Q1", "answer": "yes", "score": 5}]}],
            "objectives": {"priorities": [{"objective_id": "O1", "rank": 1, "key": True, "top_rating": 12,
                                           "risk_ids": ["r1"], "text": "Do the thing", "score": 12}],
                           "key_objectives": ["O1"], "risks": [{"pid": "r1", "position": 1, "name": "Risk",
                                                                "rating": 12, "comment": "high"}]},
            "coverage": [{"objective_id": "O1", "text": "Do the thing", "key": True, "linked": False,
                          "tests": [], "controls": [], "reviewed_by": None}],
            "evaluations": [],
            "blocks": _blocks("cover", "key_figures", "control_objectives", "summary_coverage"),
            "selection": {"period": "2026-Q3"},
            "generated_at": "2026-11-17T00:00:00+00:00",
        }

    async def test_pdf_is_a_real_pdf(self):
        blob, ctype = await report_render.render_document(self._snapshot(), "pdf")
        self.assertTrue(blob.startswith(b"%PDF-"))
        self.assertEqual(ctype, "application/pdf")

    async def test_docx_is_a_real_zip(self):
        blob, ctype = await report_render.render_document(self._snapshot(), "docx")
        self.assertTrue(blob.startswith(b"PK\x03\x04"))
        self.assertIn("wordprocessingml", ctype)

    async def test_html_carries_block_content(self):
        blob, ctype = await report_render.render_document(self._snapshot(), "html")
        self.assertIn("text/html", ctype)
        text = blob.decode("utf-8")
        self.assertIn("Demo", text)
        self.assertIn("Do the thing", text)

    async def test_block_order_is_the_layout_order(self):
        snap = self._snapshot()
        snap["blocks"] = _blocks("chapter", "test_results", "cover")
        sections = await renderer_document.build_sections(snap)
        self.assertEqual([s["type_id"] for s in sections], ["chapter", "test_results", "cover"])
        self.assertEqual(sections[0]["kind"], "chapter")
        self.assertEqual(sections[-1]["kind"], "cover")


# --- layout endpoints -----------------------------------------------------------


class ReportLayoutEndpointsTest(TestCase):

    async def test_list_returns_builtins_first(self):
        project = await _project()
        await ReportLayout.objects.acreate(project=project, name="Mine", blocks=_blocks("cover"))
        listing = (await report_client.get(f"/{project.pid}/report-layouts")).json()
        self.assertEqual(listing[0]["id"], "builtin-summary")
        self.assertTrue(listing[0]["built_in"])
        self.assertEqual(listing[-1]["name"], "Mine")
        self.assertFalse(listing[-1]["built_in"])

    async def test_create_update_delete_round_trip(self):
        project = await _project()
        created = (
            await report_client.post(
                f"/{project.pid}/report-layouts",
                json={"name": "Custom", "blocks": _blocks("cover", "chapter")},
            )
        ).json()
        self.assertEqual(created["revision"], 1)
        layout_id = created["id"]

        updated = (
            await report_client.put(
                f"/{project.pid}/report-layouts/{layout_id}",
                json={"name": "Custom 2", "blocks": _blocks("cover", "chapter", "test_runs")},
            )
        ).json()
        self.assertEqual(updated["name"], "Custom 2")
        self.assertEqual(updated["revision"], 2)
        self.assertEqual(len(updated["blocks"]), 3)

        resp = await report_client.delete(f"/{project.pid}/report-layouts/{layout_id}")
        self.assertEqual(resp.status_code, 204)
        self.assertIsNone(await ReportLayout.objects.filter(pid=layout_id).afirst())

    async def test_create_rejects_invalid_blocks(self):
        project = await _project()
        resp = await report_client.post(
            f"/{project.pid}/report-layouts",
            json={"name": "Bad", "blocks": [{"instance_id": "x", "block_type": "cover", "options": {}}]},
        )
        self.assertEqual(resp.status_code, 422)

    async def test_builtins_are_read_only(self):
        project = await _project()
        resp = await report_client.put(
            f"/{project.pid}/report-layouts/builtin-summary",
            json={"name": "x", "blocks": []},
        )
        self.assertEqual(resp.status_code, 409)
        resp = await report_client.delete(f"/{project.pid}/report-layouts/builtin-summary")
        self.assertEqual(resp.status_code, 409)

    async def test_duplicate_builtin_gives_a_fresh_layout(self):
        project = await _project()
        dup = (
            await report_client.post(f"/{project.pid}/report-layouts/builtin-summary/duplicate", json={})
        ).json()
        self.assertFalse(dup["built_in"])
        self.assertEqual(dup["name"], "Summary (copy)")
        original_ids = {b["instance_id"] for b in report_blocks.builtin_layout("builtin-summary")["blocks"]}
        self.assertFalse(original_ids & {b["instance_id"] for b in dup["blocks"]})

    async def test_export_import_round_trip_between_projects(self):
        source = await _project("source")
        target = await _project("target")
        dup = (
            await report_client.post(f"/{source.pid}/report-layouts/builtin-assessment-report/duplicate", json={})
        ).json()
        exported = (await report_client.get(f"/{source.pid}/report-layouts/{dup['id']}/export")).json()
        self.assertEqual(exported["format"], "aisc-report-layout")
        imported = (
            await report_client.post(f"/{target.pid}/report-layouts/import", json=exported)
        ).json()
        self.assertEqual(imported["name"], dup["name"])
        self.assertEqual(
            [b["block_type"] for b in imported["blocks"]],
            [b["block_type"] for b in exported["blocks"]],
        )

    async def test_viewer_reads_but_cannot_write(self):
        project = await _project(role=MembershipRole.Viewer)
        self.assertEqual((await report_client.get(f"/{project.pid}/report-layouts")).status_code, 200)
        resp = await report_client.post(
            f"/{project.pid}/report-layouts", json={"name": "x", "blocks": []}
        )
        self.assertEqual(resp.status_code, 403)

    async def test_stranger_gets_404(self):
        project = await Project.objects.acreate(name="x", description="", status="Ready")
        self.assertEqual((await report_client.get(f"/{project.pid}/report-layouts")).status_code, 404)

    async def test_preview_renders_html_without_saving(self):
        project = await _project()
        resp = await report_client.post(
            f"/{project.pid}/report-layouts/builtin-summary/preview", json={}
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertIn("<!doctype html>", body["html"].lower())
        self.assertGreater(len(body["outline"]), 0)
        self.assertEqual(await Report.objects.acount(), 0)

    async def test_block_types_palette(self):
        project = await _project()
        body = (await report_client.get(f"/{project.pid}/report-block-types")).json()
        ids = {t["type_id"] for g in body["groups"] for t in g["types"]}
        self.assertIn("cover", ids)
        self.assertIn("control_objectives", ids)
        self.assertEqual(body["max_blocks"], report_blocks.MAX_BLOCKS)


# --- generation: real documents, versions, immutability -------------------------


class ReportGenerationTest(TestCase):

    async def test_generate_pdf_is_a_real_pdf(self):
        project = await _project()
        body = (
            await _generate(
                project.pid,
                {"period": "2026-Q3", "format": "pdf", "layout_id": "builtin-summary"},
            )
        ).json()
        report = await Report.objects.aget(pid=body["pid"])
        self.assertTrue(bytes(report.document).startswith(b"%PDF-"))
        self.assertEqual(report.snapshot_checksum, body["snapshot_checksum"])
        # the document checksum is set by the worker, after the pending response
        self.assertTrue(report.document_checksum)
        self.assertEqual(body["layout_id"], "builtin-summary")

        resp = await report_client.get(f"/{project.pid}/reports/{body['pid']}/pdf")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp["Content-Type"], "application/pdf")
        self.assertTrue(resp.content.startswith(b"%PDF-"))

    async def test_generate_docx_and_pdf_companion(self):
        project = await _project()
        body = (
            await _generate(
                project.pid,
                {"period": "", "format": "docx", "layout_id": "builtin-assessment-report"},
            )
        ).json()
        report = await Report.objects.aget(pid=body["pid"])
        self.assertTrue(bytes(report.document).startswith(b"PK\x03\x04"))
        self.assertTrue(bytes(report.document_pdf).startswith(b"%PDF-"))

        download = await report_client.get(f"/{project.pid}/reports/{body['pid']}/download")
        self.assertIn("wordprocessingml", download["Content-Type"])
        self.assertTrue(download.content.startswith(b"PK\x03\x04"))
        pdf = await report_client.get(f"/{project.pid}/reports/{body['pid']}/pdf")
        self.assertTrue(pdf.content.startswith(b"%PDF-"))

    async def test_selects_the_named_version(self):
        project, v1, v2 = await _versioned_project()
        body = (
            await _generate(
                project.pid,
                {"format": "pdf", "version_pid": str(v1.pid), "layout_id": "builtin-summary"},
            )
        ).json()
        self.assertEqual(body["system_version"]["number"], 1)
        report = await Report.objects.aget(pid=body["pid"])
        self.assertEqual(report.system_version_id, v1.pk)
        self.assertEqual(report.snapshot["selection"]["version_pid"], str(v1.pid))

    async def test_snapshot_is_frozen_when_the_project_changes(self):
        project, v1, _ = await _versioned_project()
        created = (
            await _generate(
                project.pid,
                {"format": "pdf", "layout_id": "builtin-assessment-report"},
            )
        ).json()
        report = await Report.objects.aget(pid=created["pid"])
        before_snapshot = report.snapshot
        before_checksum = report.snapshot_checksum
        before_document = bytes(report.document)

        # the project changes under the report's feet
        project.description = "changed after the report"
        await project.asave()
        await ControlObjective.objects.acreate(
            objective_id="O99", macro_requirement="REQ", text="New objective", assessment_mode="Control",
        )
        objective = await ControlObjective.objects.aget(objective_id="O1")
        await EvidenceLink.objects.acreate(
            project=project, version=v1, objective=objective, kind="test", key="new-plugin",
        )

        # the stored report is untouched
        fresh = await Report.objects.aget(pid=created["pid"])
        self.assertEqual(fresh.snapshot, before_snapshot)
        self.assertEqual(fresh.snapshot_checksum, before_checksum)
        self.assertEqual(bytes(fresh.document), before_document)

        # and the API answers the frozen snapshot, not the live project
        view = (await report_client.get(f"/{project.pid}/reports/{created['pid']}/snapshot")).json()
        self.assertEqual(view["snapshot"], before_snapshot)
        self.assertEqual(view["snapshot_checksum"], before_checksum)
        self.assertNotIn("changed after the report", view["snapshot"]["project"]["description"])
        # O1 was unlinked when the report was frozen, even though a link now exists
        o1 = next(c for c in view["snapshot"]["coverage"] if c["objective_id"] == "O1")
        self.assertFalse(o1["linked"])

        pdf = await report_client.get(f"/{project.pid}/reports/{created['pid']}/pdf")
        self.assertEqual(pdf.content, before_document)

    async def test_project_layout_is_linked_to_the_report(self):
        project = await _project()
        layout = (
            await report_client.post(
                f"/{project.pid}/report-layouts",
                json={"name": "Custom", "blocks": _blocks("cover", "key_figures")},
            )
        ).json()
        body = (
            await _generate(
                project.pid,
                {"format": "pdf", "layout_id": layout["id"]},
            )
        ).json()
        self.assertEqual(body["layout_id"], layout["id"])
        self.assertEqual(body["layout_revision"], 1)
        report = await Report.objects.aget(pid=body["pid"])
        row = await ReportLayout.objects.aget(pid=layout["id"])
        self.assertEqual(report.layout_id, row.pk)

    async def test_stranger_cannot_generate_or_read_snapshot(self):
        project = await Project.objects.acreate(name="x", description="", status="Ready")
        resp = await report_client.post(f"/{project.pid}/reports/generate", json={"format": "pdf"})
        self.assertEqual(resp.status_code, 404)
        resp = await report_client.get(
            f"/{project.pid}/reports/00000000-0000-0000-0000-000000000000/snapshot"
        )
        self.assertEqual(resp.status_code, 404)

    async def test_viewer_cannot_generate(self):
        project = await _project(role=MembershipRole.Viewer)
        resp = await report_client.post(f"/{project.pid}/reports/generate", json={"format": "pdf"})
        self.assertEqual(resp.status_code, 403)
