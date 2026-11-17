"""
Router tests for the versioned evidence map (EVD-01): the map reads, the link
write (both ends project-scoped), independent review, version scoping and
carry-forward. A stranger gets 404, a viewer reads, an editor writes.
"""
from django.test import TestCase
from ninja.testing import TestAsyncClient

from aisc_backend.models.control import (
    ControlQuestion,
    ControlSource,
    ControlTemplate,
    ProjectChecklist,
    Submission,
    SubmissionAnswer,
)
from aisc_backend.models.control_objective import (
    ControlObjective,
    ObjectiveProfile,
    Risk,
    RiskObjectiveMapping,
)
from aisc_backend.models.evaluation import Evaluation, EvaluationPlugin
from aisc_backend.models.plugin import Plugin, PluginConfig
from aisc_backend.models.project import Project
from aisc_backend.models.project_member import MembershipRole, ProjectMember
from aisc_backend.repositories.system_repository import SystemRepository
from aisc_backend.routers.evidence import router as evidence_router

client = TestAsyncClient(evidence_router)


async def _project(name="evidence-project", role=MembershipRole.Owner):
    project = await Project.objects.acreate(name=name, description="", status="Ready")
    await ProjectMember.objects.acreate(
        project=project, subject="development", role=role,
    )
    return project


async def _objectives(*ids):
    for objective_id in ids:
        await ControlObjective.objects.aupdate_or_create(
            objective_id=objective_id,
            defaults={
                "macro_requirement": "REQ2 Technical Robustness and Safety",
                "text": f"objective {objective_id}",
                "assessment_mode": "Control + Test",
            },
        )


async def _version(name="card"):
    repo = SystemRepository()

    async def make(project):
        system = await repo.get_or_create(project)
        return await repo.create_version(system, name=name, provider="aisc-qualification")

    return make


async def _plugin(project, package_name="fairness", enabled=True):
    return await Plugin.objects.acreate(
        project=project,
        name=package_name,
        description="",
        package_name=package_name,
        version="1.0",
        display_name=package_name.title(),
        enabled=enabled,
    )


async def _run(project, plugin, status="Done"):
    evaluation = await Evaluation.objects.acreate(project=project, status="Done")
    config = await PluginConfig.objects.acreate(
        plugin=plugin, name="cfg", description="", config={}
    )
    await EvaluationPlugin.objects.acreate(
        evaluation=evaluation, plugin_config=config, name="ep", description="",
        status=status,
    )


async def _checklist(project, catalogue_id="risk-checklist", answered=0, score=4):
    source, _ = await ControlSource.objects.aupdate_or_create(
        name="AESIA", defaults={"slug": "aesia"},
    )
    template, _ = await ControlTemplate.objects.aupdate_or_create(
        catalogue_id=catalogue_id,
        defaults={"name": catalogue_id, "description": "", "source": source},
    )
    questions = []
    for order, text in enumerate(["q one", "q two"], start=1):
        q, _ = await ControlQuestion.objects.aupdate_or_create(
            template=template, order=order, defaults={"text": text},
        )
        questions.append(q)
    checklist = await ProjectChecklist.objects.acreate(
        project=project, name="cl", description="", template=template,
    )
    if answered:
        submission = await Submission.objects.acreate(
            project=project, checklist=checklist, status="Closed", version=1,
        )
        for q in questions[:answered]:
            await SubmissionAnswer.objects.acreate(
                submission=submission, question=q, answer="", score=score,
            )
    return checklist


class EvidenceMapRouterTest(TestCase):

    async def test_stranger_gets_404_and_viewer_reads_only(self):
        stranger_project = await _project("stranger")
        await _objectives("O1")
        # development is a member of every project it creates; drop the membership
        await ProjectMember.objects.filter(project=stranger_project).adelete()
        resp = await client.get(f"/{stranger_project.pid}/evidence")
        self.assertEqual(resp.status_code, 404, resp.text)

        viewer_project = await _project("viewer-project", role=MembershipRole.Viewer)
        read = await client.get(f"/{viewer_project.pid}/evidence")
        self.assertEqual(read.status_code, 200, read.text)
        self.assertFalse(read.json()["can_edit"])

    async def test_map_lists_objectives_tests_and_controls(self):
        project = await _project()
        await _objectives("O1", "O2", "O3")
        await ObjectiveProfile.objects.acreate(
            project=project, name="", objectives=["O1", "O2"], key_objectives=["O1"],
        )
        plugin = await _plugin(project, "fairness")
        await _run(project, plugin, status="Done")
        await _checklist(project, answered=1)
        version = await (await _version())(project)

        resp = await client.get(f"/{project.pid}/evidence")
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertEqual(body["version"]["number"], 1)
        self.assertEqual([o["id"] for o in body["objectives"]], ["O1", "O2"])
        o1 = body["objectives"][0]
        self.assertEqual(o1["dimension"], "technical-robustness-safety")
        self.assertTrue(o1["priority"])
        self.assertEqual(o1["evidence"], "not covered")
        self.assertEqual([t["key"] for t in body["tests"]], ["fairness"])
        self.assertEqual(body["tests"][0]["runs"]["executed"], 1)
        self.assertEqual(len(body["controls"]), 1)
        self.assertEqual(body["controls"][0]["submission"]["answered"], 1)
        self.assertEqual(body["coverage"], {"linked": 0, "objectives": 2, "gaps": 2})

    async def test_priority_and_stale_feed_the_gaps_and_priority_filters(self):
        """The two filters read three per-objective fields; here is where they come from.

        `priority` is earned: it is the risks the assessor bound to the objective
        (a rating of High or worse makes it key by default), an explicit key
        choice, or nothing — it is never a default. `linked` says a saved link
        names it. `stale` says this version no longer selects it while a saved link
        still does. The pilot's page filters on exactly those:

            (!gaps || (!o.linked && !o.stale)) && (!prio || o.priority)
        """
        project = await _project("filters")
        await _objectives("O1", "O2", "O3", "O4")
        # the version selects O1..O3; O4 is left out of step 2's selection
        await ObjectiveProfile.objects.acreate(
            project=project, name="", objectives=["O1", "O2", "O3"], key_objectives=[],
        )
        objectives = {
            o.objective_id: o
            async for o in ControlObjective.objects.filter(project__isnull=True)
        }
        critical = await Risk.objects.acreate(
            project=project, position=1, impact=5, likelihood=5,
        )
        low = await Risk.objects.acreate(project=project, position=2, impact=1, likelihood=1)
        await RiskObjectiveMapping.objects.acreate(
            project=project, risk=critical, objective=objectives["O1"],
        )
        await RiskObjectiveMapping.objects.acreate(
            project=project, risk=low, objective=objectives["O2"],
        )
        await _plugin(project, "fairness")
        version = await (await _version())(project)

        # O4 is not selected, so it is not on the map at all until a link names it
        body = (await client.get(f"/{project.pid}/evidence")).json()
        self.assertEqual([o["id"] for o in body["objectives"]], ["O1", "O2", "O3"])
        before = {o["id"]: o for o in body["objectives"]}
        self.assertTrue(before["O1"]["priority"])       # mitigates a 25 (Critical) risk
        self.assertFalse(before["O2"]["priority"])      # mitigates a 1 (Low) risk
        self.assertFalse(before["O3"]["priority"])      # no risk maps to it
        self.assertIsNone(before["O3"]["stale"])

        link = await client.put(
            f"/{project.pid}/evidence/links",
            json={
                "version": str(version.pid),
                "links": [
                    {"objective_id": "O1", "kind": "test", "key": "fairness", "note": None},
                    {"objective_id": "O4", "kind": "test", "key": "fairness",
                     "note": "kept from the version above"},
                ],
            },
        )
        self.assertEqual(link.status_code, 200, link.text)
        linked = link.json()
        rows = {o["id"]: o for o in linked["objectives"]}
        # a link keeps O4 on the map, marked as no longer selected
        self.assertEqual(sorted(rows), ["O1", "O2", "O3", "O4"])
        self.assertEqual(rows["O4"]["stale"], "not selected")
        self.assertTrue(rows["O4"]["linked"])
        self.assertIsNone(rows["O2"]["stale"])
        self.assertEqual(linked["coverage"], {"linked": 2, "objectives": 4, "gaps": 2})

        # the row sets the page's filters leave, read off the payload alone
        shown = linked["objectives"]
        self.assertEqual(
            [o["id"] for o in shown if not o["linked"] and not o["stale"]], ["O2", "O3"]
        )
        self.assertEqual([o["id"] for o in shown if o["priority"]], ["O1"])
        self.assertEqual(
            [
                o["id"]
                for o in shown
                if (not o["linked"] and not o["stale"]) and o["priority"]
            ],
            [],
        )

    async def test_link_note_and_unlink_roundtrip(self):
        project = await _project()
        await _objectives("O1")
        await ObjectiveProfile.objects.acreate(
            project=project, name="", objectives=["O1"],
        )
        await _plugin(project, "fairness")
        checklist = await _checklist(project)
        version = await (await _version())(project)

        put = await client.put(
            f"/{project.pid}/evidence/links",
            json={
                "version": str(version.pid),
                "links": [
                    {"objective_id": "O1", "kind": "test", "key": "fairness", "note": "holds"},
                    {"objective_id": "O1", "kind": "control",
                     "key": str(checklist.pid), "note": None},
                ],
            },
        )
        self.assertEqual(put.status_code, 200, put.text)
        body = put.json()
        self.assertEqual(len(body["links"]), 2)
        by_kind = {l["kind"]: l for l in body["links"]}
        self.assertEqual(by_kind["test"]["note"], "holds")
        self.assertEqual(body["coverage"]["linked"], 1)
        self.assertTrue(body["objectives"][0]["linked"])

        # the checklist is answered → the control link shows attention (partial)
        self.assertEqual(body["objectives"][0]["evidence"], "no evidence")

        unlink = await client.put(
            f"/{project.pid}/evidence/links",
            json={"version": str(version.pid), "links": []},
        )
        self.assertEqual(unlink.status_code, 200, unlink.text)
        self.assertEqual(unlink.json()["links"], [])
        self.assertEqual(unlink.json()["coverage"]["linked"], 0)

    async def test_rejects_unknown_and_cross_project_links(self):
        project = await _project()
        other = await _project("other-project")
        await _objectives("O1")
        await ObjectiveProfile.objects.acreate(project=project, name="", objectives=["O1"])
        await _plugin(project, "fairness")
        version = await (await _version())(project)
        other_checklist = await _checklist(other)

        bad_objective = await client.put(
            f"/{project.pid}/evidence/links",
            json={"version": str(version.pid),
                  "links": [{"objective_id": "O99", "kind": "test", "key": "fairness"}]},
        )
        self.assertEqual(bad_objective.status_code, 400, bad_objective.text)

        bad_key = await client.put(
            f"/{project.pid}/evidence/links",
            json={"version": str(version.pid),
                  "links": [{"objective_id": "O1", "kind": "control",
                             "key": str(other_checklist.pid)}]},
        )
        self.assertEqual(bad_key.status_code, 400, bad_key.text)

    async def test_older_version_is_read_only_and_unchanged(self):
        project = await _project()
        await _objectives("O1")
        await ObjectiveProfile.objects.acreate(project=project, name="", objectives=["O1"])
        await _plugin(project, "fairness")
        make = await _version()
        v1 = await make(project)
        await client.put(
            f"/{project.pid}/evidence/links",
            json={"version": str(v1.pid),
                  "links": [{"objective_id": "O1", "kind": "test", "key": "fairness"}]},
        )
        v2 = await make(project)

        old = await client.get(f"/{project.pid}/evidence?version={v1.pid}")
        self.assertEqual(old.status_code, 200, old.text)
        self.assertTrue(old.json()["read_only"])
        self.assertFalse(old.json()["can_edit"])
        self.assertEqual(len(old.json()["links"]), 1)

        refused = await client.put(
            f"/{project.pid}/evidence/links",
            json={"version": str(v1.pid),
                  "links": [{"objective_id": "O1", "kind": "test", "key": "fairness"}]},
        )
        self.assertEqual(refused.status_code, 403, refused.text)

    async def test_carry_forward_offer_then_copy(self):
        project = await _project()
        await _objectives("O1")
        await ObjectiveProfile.objects.acreate(project=project, name="", objectives=["O1"])
        await _plugin(project, "fairness")
        make = await _version()
        v1 = await make(project)
        await client.put(
            f"/{project.pid}/evidence/links",
            json={"version": str(v1.pid),
                  "links": [{"objective_id": "O1", "kind": "test", "key": "fairness",
                             "note": "v1 evidence"}]},
        )
        v2 = await make(project)

        offered = (await client.get(f"/{project.pid}/evidence")).json()
        self.assertEqual(offered["version"]["number"], 2)
        self.assertEqual(offered["carried_from"], 1)
        self.assertEqual(len(offered["links"]), 1)
        self.assertEqual(offered["links"][0]["note"], "v1 evidence")
        # offered, not saved: the map itself has no links
        self.assertFalse(offered["objectives"][0]["linked"])

        copied = await client.put(
            f"/{project.pid}/evidence/links",
            json={"version": str(v2.pid),
                  "links": [{"objective_id": "O1", "kind": "test", "key": "fairness",
                             "note": "v1 evidence"}]},
        )
        self.assertEqual(copied.status_code, 200, copied.text)
        self.assertIsNone(copied.json()["carried_from"])
        self.assertTrue(copied.json()["objectives"][0]["linked"])

        # the old version is untouched
        old = (await client.get(f"/{project.pid}/evidence?version={v1.pid}")).json()
        self.assertEqual(len(old["links"]), 1)

    async def test_review_requires_independence_and_is_invalidated_on_change(self):
        project = await _project()
        await _objectives("O1")
        await ObjectiveProfile.objects.acreate(project=project, name="", objectives=["O1"])
        await _plugin(project, "fairness")
        version = await (await _version())(project)

        # development authors a link → development may not review it
        await client.put(
            f"/{project.pid}/evidence/links",
            json={"version": str(version.pid),
                  "links": [{"objective_id": "O1", "kind": "test", "key": "fairness"}]},
        )
        refused = await client.post(
            f"/{project.pid}/evidence/review", json={"version": str(version.pid)},
        )
        self.assertEqual(refused.status_code, 403, refused.text)

        # a link made by someone else → development reviews
        from aisc_backend.models.evidence import EvidenceLink

        await EvidenceLink.objects.filter(version=version).aupdate(created_by="someone-else")
        ok = await client.post(
            f"/{project.pid}/evidence/review", json={"version": str(version.pid)},
        )
        self.assertEqual(ok.status_code, 200, ok.text)
        self.assertIsNotNone(ok.json()["review"])
        self.assertTrue(ok.json()["can_review"])  # made none of the links → may review

        # changing the links invalidates the review
        changed = await client.put(
            f"/{project.pid}/evidence/links",
            json={"version": str(version.pid),
                  "links": [{"objective_id": "O1", "kind": "test", "key": "fairness",
                             "note": "changed"}]},
        )
        self.assertIsNone(changed.json()["review"])
