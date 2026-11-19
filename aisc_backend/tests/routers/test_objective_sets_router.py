"""
A project's own objective sets: draft, published numbered versions, apply.

The draft is editable; publishing freezes it as version 1..n and never touches
an old version; applying a published version copies its rows into the project's
own catalogue and selects them in the profile, so the matrix, the evidence map
and the report read them like any other objective.
"""
from django.test import TestCase
from ninja.testing import TestAsyncClient

from aisc_control_objectives.models import ControlObjective, ObjectiveProfile, ObjectiveSetVersion
from aisc_backend.models.project import Project
from aisc_backend.models.project_member import MembershipRole, ProjectMember
from aisc_backend.routers.control_objectives import router as co_router

co_client = TestAsyncClient(co_router)

DRAFT = [
    {"objective_id": "CX-1", "macro_requirement": "House rule", "text": "Keep a model card"},
    {"objective_id": "CX-2", "macro_requirement": "House rule", "text": "Log every decision"},
]


async def _project(name="sets", role=MembershipRole.Owner):
    project = await Project.objects.acreate(name=name, description="", status="Ready")
    if role is not None:
        await ProjectMember.objects.acreate(
            project=project, subject="development", role=role,
        )
    return project


async def _create(project, draft=None):
    resp = await co_client.post(
        f"/{project.pid}/objective-sets",
        json={"name": "House rules", "code_prefix": "CX",
              "draft": DRAFT if draft is None else draft},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


class ObjectiveSetsRouterTest(TestCase):

    async def test_a_new_set_is_a_draft_and_publishes_as_version_1(self):
        project = await _project()
        body = await _create(project)
        self.assertEqual(body["published_version"], None)
        self.assertEqual([r["objective_id"] for r in body["draft"]], ["CX-1", "CX-2"])

        published = await co_client.post(
            f"/{project.pid}/objective-sets/{body['pid']}/publish"
        )
        self.assertEqual(published.status_code, 200, published.text)
        published = published.json()
        self.assertEqual(published["published_version"], 1)
        self.assertEqual([v["number"] for v in published["versions"]], [1])
        self.assertEqual(
            [r["objective_id"] for r in published["versions"][0]["objectives"]],
            ["CX-1", "CX-2"],
        )

    async def test_a_published_version_is_frozen_and_the_next_publish_is_version_2(self):
        project = await _project()
        body = await _create(project)
        await co_client.post(f"/{project.pid}/objective-sets/{body['pid']}/publish")

        edited = await co_client.put(
            f"/{project.pid}/objective-sets/{body['pid']}",
            json={"name": "House rules", "code_prefix": "CX", "draft": DRAFT[:1]},
        )
        self.assertEqual(edited.status_code, 200, edited.text)
        second = (await co_client.post(
            f"/{project.pid}/objective-sets/{body['pid']}/publish"
        )).json()
        self.assertEqual(second["published_version"], 2)
        versions = {v["number"]: v for v in second["versions"]}
        # v1 still carries the rows it was published with
        self.assertEqual(len(versions[1]["objectives"]), 2)
        self.assertEqual(len(versions[2]["objectives"]), 1)

    async def test_apply_copies_a_published_version_into_the_project_and_selects_it(self):
        project = await _project()
        body = await _create(project)
        await co_client.post(f"/{project.pid}/objective-sets/{body['pid']}/publish")

        applied = await co_client.post(
            f"/{project.pid}/objective-sets/{body['pid']}/apply", json={}
        )
        self.assertEqual(applied.status_code, 200, applied.text)
        applied = applied.json()
        self.assertEqual(applied["version"], 1)
        self.assertEqual(applied["created"], ["CX-1", "CX-2"])
        self.assertEqual(applied["updated"], [])

        rows = {
            o.objective_id: o
            async for o in ControlObjective.objects.filter(project=project)
        }
        self.assertEqual(set(rows), {"CX-1", "CX-2"})
        self.assertEqual(rows["CX-1"].text, "Keep a model card")

        profile = await ObjectiveProfile.objects.filter(project=project).afirst()
        self.assertEqual(profile.objectives, ["CX-1", "CX-2"])

    async def test_applying_twice_updates_instead_of_duplicating(self):
        project = await _project()
        body = await _create(project)
        await co_client.post(f"/{project.pid}/objective-sets/{body['pid']}/publish")
        await co_client.post(f"/{project.pid}/objective-sets/{body['pid']}/apply", json={})
        again = (await co_client.post(
            f"/{project.pid}/objective-sets/{body['pid']}/apply", json={}
        )).json()
        self.assertEqual(again["created"], [])
        self.assertEqual(again["updated"], ["CX-1", "CX-2"])
        self.assertEqual(await ControlObjective.objects.filter(project=project).acount(), 2)

    async def test_an_older_published_version_can_be_applied_by_number(self):
        project = await _project()
        body = await _create(project)
        await co_client.post(f"/{project.pid}/objective-sets/{body['pid']}/publish")
        await co_client.put(
            f"/{project.pid}/objective-sets/{body['pid']}",
            json={"name": "House rules", "code_prefix": "CX", "draft": DRAFT[:1]},
        )
        await co_client.post(f"/{project.pid}/objective-sets/{body['pid']}/publish")

        applied = (await co_client.post(
            f"/{project.pid}/objective-sets/{body['pid']}/apply", json={"number": 1}
        )).json()
        self.assertEqual(applied["version"], 1)
        self.assertEqual(set(applied["created"]), {"CX-1", "CX-2"})

    async def test_a_set_may_not_shadow_a_catalogue_objective(self):
        project = await _project()
        body = await _create(project, draft=[{"objective_id": "O1", "text": "shadow"}])
        await co_client.post(f"/{project.pid}/objective-sets/{body['pid']}/publish")
        resp = await co_client.post(
            f"/{project.pid}/objective-sets/{body['pid']}/apply", json={}
        )
        self.assertEqual(resp.status_code, 409)
        self.assertIn("may not shadow", resp.json()["detail"])

    async def test_a_draft_row_needs_a_code_and_codes_are_unique_in_the_set(self):
        project = await _project()
        missing = await co_client.post(
            f"/{project.pid}/objective-sets",
            json={"name": "bad", "draft": [{"objective_id": "", "text": "x"}]},
        )
        self.assertEqual(missing.status_code, 400)
        twice = await co_client.post(
            f"/{project.pid}/objective-sets",
            json={"name": "bad", "draft": [DRAFT[0], DRAFT[0]]},
        )
        self.assertEqual(twice.status_code, 400)

    async def test_publishing_an_empty_draft_is_refused(self):
        project = await _project()
        body = await _create(project, draft=[])
        resp = await co_client.post(f"/{project.pid}/objective-sets/{body['pid']}/publish")
        self.assertEqual(resp.status_code, 400)

    async def test_applying_before_publishing_is_refused(self):
        project = await _project()
        body = await _create(project)
        resp = await co_client.post(
            f"/{project.pid}/objective-sets/{body['pid']}/apply", json={}
        )
        self.assertEqual(resp.status_code, 400)

    async def test_two_projects_may_each_keep_a_set_of_the_same_name(self):
        first = await _project("sets-a")
        second = await _project("sets-b")
        await _create(first)
        await _create(second)
        self.assertEqual(await ObjectiveSetVersion.objects.acount(), 0)
        listed = (await co_client.get(f"/{second.pid}/objective-sets")).json()
        self.assertEqual([s["name"] for s in listed], ["House rules"])

    async def test_deleting_a_set_drops_its_versions_but_keeps_applied_rows(self):
        project = await _project()
        body = await _create(project)
        await co_client.post(f"/{project.pid}/objective-sets/{body['pid']}/publish")
        await co_client.post(f"/{project.pid}/objective-sets/{body['pid']}/apply", json={})
        resp = await co_client.delete(f"/{project.pid}/objective-sets/{body['pid']}")
        self.assertEqual(resp.status_code, 204)
        self.assertEqual(await ObjectiveSetVersion.objects.acount(), 0)
        self.assertEqual(await ControlObjective.objects.filter(project=project).acount(), 2)

    async def test_a_viewer_may_read_but_not_write_and_a_stranger_gets_404(self):
        viewer = await _project("sets-viewer", role=MembershipRole.Viewer)
        read = await co_client.get(f"/{viewer.pid}/objective-sets")
        self.assertEqual(read.status_code, 200)
        write = await co_client.post(
            f"/{viewer.pid}/objective-sets", json={"name": "nope", "draft": []}
        )
        self.assertEqual(write.status_code, 403)

        stranger = await _project("sets-stranger", role=None)
        resp = await co_client.get(f"/{stranger.pid}/objective-sets")
        self.assertEqual(resp.status_code, 404)

    async def test_another_projects_set_is_not_reachable(self):
        first = await _project("sets-owner")
        second = await _project("sets-other")
        body = await _create(first)
        resp = await co_client.get(f"/{second.pid}/objective-sets/{body['pid']}")
        self.assertEqual(resp.status_code, 404)


class ObjectiveSetDiffRouterTest(TestCase):
    """The history view: what changed between a published version and the draft."""

    async def test_the_default_comparison_is_the_published_version_against_the_draft(self):
        project = await _project("sets-diff")
        body = await _create(project)
        await co_client.post(f"/{project.pid}/objective-sets/{body['pid']}/publish")

        # reword CX-1, drop CX-2, add CX-3 in the draft
        draft = [
            dict(DRAFT[0], text="Keep a signed model card"),
            {"objective_id": "CX-3", "text": "Review annually"},
        ]
        await co_client.put(
            f"/{project.pid}/objective-sets/{body['pid']}",
            json={"name": body["name"], "code_prefix": "CX", "draft": draft},
        )

        resp = await co_client.get(f"/{project.pid}/objective-sets/{body['pid']}/diff")
        self.assertEqual(resp.status_code, 200, resp.text)
        diff = resp.json()
        self.assertEqual(diff["from_version"], 1)
        self.assertIsNone(diff["to_version"])          # the draft
        self.assertEqual(diff["added"], ["CX-3"])
        self.assertEqual(diff["removed"], ["CX-2"])
        self.assertEqual([c["objective_id"] for c in diff["changed"]], ["CX-1"])
        self.assertEqual(diff["changed"][0]["fields"][0]["field"], "text")

    async def test_two_published_versions_can_be_compared(self):
        project = await _project("sets-diff-versions")
        body = await _create(project)
        url = f"/{project.pid}/objective-sets/{body['pid']}"
        await co_client.post(f"{url}/publish")
        await co_client.put(url, json={
            "name": body["name"], "code_prefix": "CX",
            "draft": [DRAFT[0], {"objective_id": "CX-4", "text": "Added later"}],
        })
        await co_client.post(f"{url}/publish")

        resp = await co_client.get(f"{url}/diff?from_version=1&to_version=2")
        self.assertEqual(resp.status_code, 200, resp.text)
        diff = resp.json()
        self.assertEqual(diff["from_version"], 1)
        self.assertEqual(diff["to_version"], 2)
        self.assertEqual(diff["added"], ["CX-4"])
        self.assertEqual(diff["removed"], ["CX-2"])

    async def test_an_unpublished_version_number_is_refused(self):
        project = await _project("sets-diff-missing")
        body = await _create(project)
        await co_client.post(f"/{project.pid}/objective-sets/{body['pid']}/publish")
        resp = await co_client.get(
            f"/{project.pid}/objective-sets/{body['pid']}/diff?from_version=7"
        )
        self.assertEqual(resp.status_code, 404)

    async def test_nothing_published_reads_as_everything_added(self):
        project = await _project("sets-diff-draft")
        body = await _create(project)
        resp = await co_client.get(f"/{project.pid}/objective-sets/{body['pid']}/diff")
        self.assertEqual(resp.status_code, 200, resp.text)
        diff = resp.json()
        self.assertIsNone(diff["from_version"])
        self.assertIsNone(diff["to_version"])
        self.assertEqual(diff["added"], ["CX-1", "CX-2"])
