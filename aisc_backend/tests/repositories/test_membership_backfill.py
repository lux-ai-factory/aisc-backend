"""
Tests for the membership repository's bootstrap_owners backfill.

A memberless project is a project nobody can open; the backfill makes the
configured subjects owners of exactly those projects, and is idempotent — a
project that already has members is untouched, so rerunning changes nothing.
"""
from django.test import TestCase

from aisc_backend.models.project import Project
from aisc_backend.models.project_member import MembershipRole, ProjectMember
from aisc_backend.repositories.membership_repository import MembershipRepository


class BootstrapOwnersTest(TestCase):

    async def test_memberless_projects_get_the_subject_owner(self):
        memberless = await Project.objects.acreate(name="empty", description="", status="Ready")
        owned = await Project.objects.acreate(name="owned", description="", status="Ready")
        await ProjectMember.objects.acreate(
            project=owned, subject="someone", role=MembershipRole.Owner
        )

        inserted = await MembershipRepository().bootstrap_owners(["admin"])

        self.assertEqual(inserted, 1)   # only the memberless one got the owner
        owner = await ProjectMember.objects.filter(
            project=memberless, subject="admin"
        ).afirst()
        self.assertEqual(owner.role, MembershipRole.Owner)

    async def test_idempotent_rerun_adds_nothing(self):
        project = await Project.objects.acreate(name="empty", description="", status="Ready")
        repo = MembershipRepository()
        await repo.bootstrap_owners(["admin"])
        again = await repo.bootstrap_owners(["admin", "other"])

        self.assertEqual(again, 0)   # the project already has members — answered for
        roles = {
            member.subject: member.role
            async for member in ProjectMember.objects.filter(project=project)
        }
        self.assertEqual(roles, {"admin": MembershipRole.Owner})
