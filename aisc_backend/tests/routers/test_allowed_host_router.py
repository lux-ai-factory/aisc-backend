"""
Router tests for the allowed internal hosts list (CON-01).

Who reads and who manages it, what an entry is normalised to, what can never be
listed however hard a caller tries, and the marked rows the page shows for an entry
that has stopped taking effect.
"""
from django.test import TestCase, override_settings
from ninja.testing import TestAsyncClient

from aisc_backend.models.connection import AllowedHost
from aisc_backend.models.project import Project
from aisc_backend.models.project_member import MembershipRole, ProjectMember
from aisc_backend.routers.allowed_host import router as allowed_host_router

client = TestAsyncClient(allowed_host_router)


async def _project(name="allowed-hosts-project", role=MembershipRole.Owner) -> Project:
    project = await Project.objects.acreate(name=name, description="", status="Ready")
    if role is not None:
        await ProjectMember.objects.acreate(project=project, subject="development", role=role)
    return project


class AllowedHostRouterTest(TestCase):

    async def test_viewer_reads_and_editor_writes_and_a_stranger_gets_404(self):
        stranger = await _project("stranger", role=None)
        self.assertEqual(
            (await client.get(f"/{stranger.pid}/allowed-hosts")).status_code, 404
        )

        viewer = await _project("viewer-project", role=MembershipRole.Viewer)
        read = await client.get(f"/{viewer.pid}/allowed-hosts")
        self.assertEqual(read.status_code, 200, read.text)
        self.assertFalse(read.json()["can_manage"])
        self.assertIn("never allowed here", read.json()["rule"])
        refused = await client.put(
            f"/{viewer.pid}/allowed-hosts/box.local", json={"note": "mine"}
        )
        self.assertEqual(refused.status_code, 403, refused.text)

        editor = await _project("editor-project", role=MembershipRole.Editor)
        written = await client.put(
            f"/{editor.pid}/allowed-hosts/box.local:8500", json={"note": "MCAS-lite"}
        )
        self.assertEqual(written.status_code, 200, written.text)
        self.assertTrue(
            (await client.get(f"/{editor.pid}/allowed-hosts")).json()["can_manage"]
        )

    @override_settings(CONNECTIONS_ALLOWED_HOSTS=["10.0.0.5:8500"])
    async def test_the_list_names_what_the_deployment_allows_for_every_project(self):
        project = await _project()
        listed = await client.get(f"/{project.pid}/allowed-hosts")
        self.assertEqual(listed.json()["floor"], ["10.0.0.5:8500"])
        self.assertEqual(listed.json()["entries"], [])

    async def test_an_entry_is_stored_normalised_with_its_note_and_who_set_it(self):
        project = await _project()
        written = await client.put(
            f"/{project.pid}/allowed-hosts/Box.Local:8500",
            json={"note": "MCAS-lite on the Docker host"},
        )
        self.assertEqual(written.status_code, 200, written.text)
        self.assertEqual(written.json()["host"], "box.local:8500")
        self.assertEqual(written.json()["note"], "MCAS-lite on the Docker host")
        self.assertEqual(written.json()["updated_by"], "development")
        self.assertIsNotNone(written.json()["updated_at"])

        row = await AllowedHost.objects.filter(project=project).afirst()
        self.assertEqual(row.host, "box.local:8500")
        # the edit is a rename of the note, not a second entry
        again = await client.put(f"/{project.pid}/allowed-hosts/box.local:8500", json={"note": "kept"})
        self.assertEqual(again.status_code, 200, again.text)
        self.assertEqual(await AllowedHost.objects.filter(project=project).acount(), 1)
        self.assertEqual((await client.get(f"/{project.pid}/allowed-hosts")).json()["entries"][0]["note"], "kept")

    async def test_a_hopeless_entry_is_refused_with_the_reason(self):
        project = await _project()
        for host, expected in (
            ("127.0.0.1", "loopback"),
            ("169.254.169.254", "metadata"),
            ("redis", "own services"),
        ):
            refused = await client.put(f"/{project.pid}/allowed-hosts/{host}", json={})
            self.assertEqual(refused.status_code, 400, refused.text)
            self.assertIn(expected, refused.text)
        # a mistyped port is refused rather than quietly dropped
        named = await client.put(f"/{project.pid}/allowed-hosts/box.local:notaport", json={})
        self.assertEqual(named.status_code, 400, named.text)
        self.assertEqual(await AllowedHost.objects.filter(project=project).acount(), 0)

    async def test_withdrawing_an_entry_removes_it_from_the_list(self):
        project = await _project()
        await client.put(f"/{project.pid}/allowed-hosts/box.local:8500", json={"note": "mine"})
        removed = await client.delete(f"/{project.pid}/allowed-hosts/box.local:8500")
        self.assertEqual(removed.status_code, 204, removed.text)
        self.assertEqual(await AllowedHost.objects.filter(project=project).acount(), 0)
        missing = await client.delete(f"/{project.pid}/allowed-hosts/box.local:8500")
        self.assertEqual(missing.status_code, 404, missing.text)

    async def test_an_entry_that_can_no_longer_take_effect_is_marked_denied(self):
        project = await _project()
        await AllowedHost.objects.acreate(
            project=project, name="127.0.0.1", description="", host="127.0.0.1", note="legacy",
        )
        entries = (await client.get(f"/{project.pid}/allowed-hosts")).json()["entries"]
        self.assertEqual(len(entries), 1)
        self.assertIn("loopback", entries[0]["denied"])
