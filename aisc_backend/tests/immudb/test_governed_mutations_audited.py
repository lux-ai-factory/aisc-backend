"""
CORE-01 slice 2: the newly governed mutations go through the ONE audit writer.

A membership change and a report generation each append an event to the single
immudb ledger (aisc_backend.audit.clerk.clerk), scoped to the project; and an
audit outage must not break the request — the existing best-effort semantics are
preserved (log_action swallows the failure).
"""
import unittest.mock as mock

from django.test import TestCase, override_settings
from ninja.testing import TestAsyncClient

from aisc_backend.audit.clerk import clerk
from aisc_backend.models.project import Project
from aisc_backend.models.project_member import MembershipRole, ProjectMember
from aisc_backend.routers.project import router as project_router
from aisc_report.routers.report import router as report_router

project_client = TestAsyncClient(project_router)
report_client = TestAsyncClient(report_router)


@override_settings(FEATURE_REPORTING=True)
class GovernedMutationsAuditedTest(TestCase):

    async def _project(self):
        project = await Project.objects.acreate(
            name="governed-project", description="", status="Ready"
        )
        await ProjectMember.objects.acreate(
            project=project, subject="development", role=MembershipRole.Owner
        )
        return project

    async def test_governed_mutations_write_through_the_one_writer_best_effort(self):
        project = await self._project()

        # A membership change is governed and audited.
        resp = await project_client.post(
            f"/{project.pid}/members",
            json={"subject": "newbie", "email": "n@example.com", "role": "viewer"},
        )
        self.assertEqual(200, resp.status_code, resp.text)

        # A report generation is governed and audited.
        with mock.patch(
            "aisc_report.services.report_service.generate",
            new=mock.AsyncMock(return_value=(b"%PDF-1.4 fake", "application/pdf")),
        ):
            resp = await report_client.post(
                f"/{project.pid}/reports/generate",
                json={"period": "2026-Q3", "format": "pdf"},
            )
        self.assertEqual(200, resp.status_code, resp.text)

        # Both events are in the ONE ledger, scoped to the project (same writer).
        events = clerk.list_events(project_id=str(project.pid))
        by_key = {(e["action"], e["resource_type"]) for e in events}
        self.assertIn(("member_added", "project_member"), by_key)
        self.assertIn(("generate", "report"), by_key)
        self.assertIn(
            "newbie",
            [e["resource_id"] for e in events if e["action"] == "member_added"],
        )

        # An audit outage must not fail the request (best-effort: log and continue).
        with mock.patch.object(clerk, "write_event", side_effect=RuntimeError("immudb down")):
            resp = await project_client.post(
                f"/{project.pid}/members",
                json={"subject": "another", "role": "viewer"},
            )
            self.assertEqual(200, resp.status_code, resp.text)
            with mock.patch(
                "aisc_report.services.report_service.generate",
                new=mock.AsyncMock(return_value=(b"%PDF-1.4 fake", "application/pdf")),
            ):
                resp = await report_client.post(
                    f"/{project.pid}/reports/generate",
                    json={"period": "2026-Q4", "format": "pdf"},
                )
            self.assertEqual(200, resp.status_code, resp.text)
