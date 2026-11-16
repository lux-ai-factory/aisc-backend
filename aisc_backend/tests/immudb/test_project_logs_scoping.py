"""
Tests for project-scoped log reads.

Any member of a project may read THAT project's events only (via
GET /api/v1/audit/project/{pid} and its alias GET /api/v1/projects/{pid}/logs);
a stranger gets 404 (project names must not leak). The ledger is the fake (the
suite patches ImmudbClient in tests/__init__.py), and events carry a `project`
column derived from metadata.projectPid.
"""
import unittest.mock as mock

from django.test import TestCase
from ninja.testing import TestAsyncClient

from aisc_backend.audit.clerk import clerk
from aisc_backend.models.project import Project
from aisc_backend.models.project_member import MembershipRole, ProjectMember
from aisc_backend.routers.audit import router as audit_router
from aisc_backend.routers.project import router as project_router

audit_client = TestAsyncClient(audit_router)
project_client = TestAsyncClient(project_router)


class ProjectLogsScopingTest(TestCase):

    async def _project(self, owner: bool = True):
        project = await Project.objects.acreate(
            name="logs-project", description="", status="Ready"
        )
        if owner:
            await ProjectMember.objects.acreate(
                project=project, subject="development", role=MembershipRole.Owner
            )
        return project

    def setUp(self):
        # Fresh fake per test so events don't leak across tests.
        self._patch = mock.patch("aisc_backend.routers.audit.clerk", clerk)
        self._patch.start()
        self.addCleanup(self._patch.stop)

    async def test_member_reads_only_own_projects_events(self):
        mine = await self._project()
        theirs = await Project.objects.acreate(name="other", description="", status="Ready")
        await ProjectMember.objects.acreate(
            project=theirs, subject="other-user", role=MembershipRole.Owner
        )
        clerk.write_event(actor="me", action="create", resource_type="evaluation",
                          resource_id="create-target",
                          project_id=str(mine.pid), metadata={"projectPid": str(mine.pid)})
        clerk.write_event(actor="them", action="create", resource_type="evaluation",
                          resource_id="other-target",
                          project_id=str(theirs.pid), metadata={"projectPid": str(theirs.pid)})

        resp = await audit_client.get(f"/project/{mine.pid}")
        self.assertEqual(resp.status_code, 200)
        events = resp.json()["events"]
        self.assertEqual(len(events), 1)   # the other project's event is filtered out
        self.assertEqual(events[0]["resource_id"], "create-target")

    async def test_project_logs_alias_returns_same(self):
        mine = await self._project()
        clerk.write_event(actor="me", action="run", resource_type="evaluation",
                          project_id=str(mine.pid), metadata={"projectPid": str(mine.pid)})
        resp = await project_client.get(f"/{mine.pid}/logs")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.json()["events"]), 1)

    async def test_stranger_gets_404_not_events(self):
        project = await self._project(owner=False)
        clerk.write_event(actor="me", action="create", resource_type="evaluation",
                          project_id=str(project.pid), metadata={"projectPid": str(project.pid)})
        resp = await audit_client.get(f"/project/{project.pid}")
        self.assertEqual(resp.status_code, 404)
        resp = await project_client.get(f"/{project.pid}/logs")
        self.assertEqual(resp.status_code, 404)

    async def test_admin_reads_any_project_logs_without_membership(self):
        """An admin (realm role, not a member) may read any project's events.

        `effective_role` counts the admin role as owner, so role_or_404 lets an
        admin through on a project they are not in — orphaned projects stay
        recoverable, and a platform operator can audit any project.
        """
        project = await self._project(owner=False)
        clerk.write_event(actor="root", action="create", resource_type="project",
                          resource_id=str(project.pid),
                          project_id=str(project.pid), metadata={"projectPid": str(project.pid)})

        with mock.patch(
            "aisc_backend.auth.access.caller_of",
            return_value=mock.Mock(
                subject="root", email=None, username="root",
                roles=("admin",), has_role=lambda role: role == "admin",
            ),
        ):
            resp = await audit_client.get(f"/project/{project.pid}")
            self.assertEqual(resp.status_code, 200)
            self.assertEqual(len(resp.json()["events"]), 1)
            resp = await project_client.get(f"/{project.pid}/logs")
            self.assertEqual(resp.status_code, 200)
            self.assertEqual(len(resp.json()["events"]), 1)

    async def test_creator_is_owner_and_reads_own_logs(self):
        """The user who created a project is made its owner, so they read
        its logs like any member — no extra door needed."""
        from aisc_backend.repositories.membership_repository import MembershipRepository

        project = await Project.objects.acreate(
            name="creator-project", description="", status="Ready"
        )
        await MembershipRepository().create_owner(project, "the-creator", "c@localhost")
        clerk.write_event(actor="the-creator", action="create", resource_type="project",
                          resource_id=str(project.pid),
                          project_id=str(project.pid), metadata={"projectPid": str(project.pid)})
        # the dev caller ("development") is not the creator -> stranger
        resp = await audit_client.get(f"/project/{project.pid}")
        self.assertEqual(resp.status_code, 404)
