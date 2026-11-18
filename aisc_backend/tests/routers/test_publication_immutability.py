"""
Explicit, immutable publication (VER-01, slice 2).

A draft is turned into a published snapshot by ONE explicit operation
(`POST .../publish`, the same operation as `.../complete`). Once published the
snapshot is immutable: editing or reopening it builds a draft/successor instead
of touching it, so an old version stays readable by its own id even after a later
draft exists — and no version or published snapshot may be deleted.

The headless service is stubbed (no network); auth is the dev bypass, so
`caller_of` returns the development caller.
"""
import unittest.mock as mock

from django.test import TestCase
from ninja.testing import TestAsyncClient

from aisc_backend.models.project import Project
from aisc_backend.models.project_member import MembershipRole, ProjectMember
from aisc_backend.models.qualification import (
    PublishedSnapshotError,
    Qualification,
    SystemVersion,
)
from aisc_backend.routers.qualification import router as qualification_router

client = TestAsyncClient(qualification_router)


async def _project():
    project = await Project.objects.acreate(
        name="publish-project", description="", status="Ready"
    )
    await ProjectMember.objects.acreate(
        project=project, subject="development", role=MembershipRole.Owner
    )
    return project


class PublicationImmutabilityTest(TestCase):

    async def test_publish_freezes_a_snapshot_later_work_cannot_change(self):
        project = await _project()
        draft = (await client.post(
            f"/{project.pid}/qualification",
            json={"questionnaire_id": "", "answers": {"q1": "yes"}},
        )).json()
        self.assertEqual(draft["status"], "draft")
        self.assertIsNone(draft["system_version"])

        # The explicit publish is the ONE action that cuts a version.
        card_v1 = {"format": "airo-min", "card": {"summary": {"label": "v1"}}}
        with mock.patch(
            "aisc_backend.services.qualification_service.build_card",
            new=mock.AsyncMock(return_value=card_v1),
        ):
            published = (await client.post(
                f"/{project.pid}/qualification/{draft['pid']}/publish",
            )).json()
        self.assertEqual(published["status"], "completed")
        self.assertEqual(published["system_version"]["number"], 1)
        self.assertEqual(published["system_card"], card_v1)
        published_pid = published["pid"]
        published_row = await Qualification.objects.aget(pid=published_pid)
        self.assertIsNotNone(published_row.published_at)

        # Editing the card freezes a successor; the published snapshot is untouched.
        edited = (await client.put(
            f"/{project.pid}/qualification/{published_pid}/card",
            json={"system_card": {"card": "edited"}},
        )).json()
        self.assertNotEqual(edited["pid"], published_pid)
        self.assertEqual(edited["system_version"]["number"], 2)

        # Reopening freezes a draft successor; the published snapshot is untouched.
        reopened = (await client.post(
            f"/{project.pid}/qualification/{published_pid}/reopen",
        )).json()
        self.assertEqual(reopened["status"], "draft")
        self.assertNotEqual(reopened["pid"], published_pid)
        self.assertIsNone(reopened["system_version"])

        # The published snapshot is exactly as it was, and still readable by its
        # old id even though a later draft now exists.
        old = (await client.get(
            f"/{project.pid}/qualification/{published_pid}"
        )).json()
        self.assertEqual(old["status"], "completed")
        self.assertEqual(old["system_card"], card_v1)
        self.assertEqual(old["system_version"]["number"], 1)

        # Its version is still readable by its old number, too.
        version = await client.get(f"/{project.pid}/system-versions/1")
        self.assertEqual(version.status_code, 200, version.text)
        self.assertEqual(version.json()["pid"], published["system_version"]["pid"])

        # A historical version cannot be deleted (append-only) ...
        historical = await SystemVersion.objects.aget(number=1)
        with self.assertRaises(PublishedSnapshotError):
            await historical.adelete()
        # ... and neither can the published snapshot.
        with self.assertRaises(PublishedSnapshotError):
            await published_row.adelete()
        self.assertTrue(
            await Qualification.objects.filter(pid=published_pid).aexists()
        )
        self.assertEqual(await SystemVersion.objects.acount(), 2)
