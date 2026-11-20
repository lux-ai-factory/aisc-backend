"""
Router tests for control objectives: the risk register, the risk-objective
mapping, and the matrix computed on read — all project-scoped (a stranger gets
404, a member reads, an editor writes).
"""
from django.test import TestCase
from ninja.testing import TestAsyncClient

from aisc_control_objectives.models import ControlObjective, ObjectiveProfile, Risk, RiskVersion
from aisc_backend.models.project import Project
from aisc_backend.models.project_member import MembershipRole, ProjectMember
from aisc_qualification.models import Qualification
from aisc_control_objectives.routers.control_objectives import router as co_router

co_client = TestAsyncClient(co_router)


async def _project():
    project = await Project.objects.acreate(name="co-project", description="", status="Ready")
    await ProjectMember.objects.acreate(
        project=project, subject="development", role=MembershipRole.Owner,
    )
    return project


async def _risk(project, name, impact=None, likelihood=None):
    """Seed one register risk straight into the DB (risks come from the AI card;
    there is no manual "add risk" endpoint any more). No revision is recorded
    yet — a version only exists for an actual assessment, so the first rating
    is v1."""
    last = await Risk.objects.filter(project=project).order_by("-position").afirst()
    position = (last.position + 1) if last is not None else 1
    return await Risk.objects.acreate(
        project=project,
        position=position,
        name=name,
        impact=impact,
        likelihood=likelihood,
    )


async def _seed_objectives():
    """A self-contained O1..O50 catalogue (the old migration CSV seed is gone).

    Idempotent: the 0032 seed migration already provisions the same rows in
    test databases, so re-creating them would hit the unique objective_id."""
    for number in range(1, 51):
        await ControlObjective.objects.aupdate_or_create(
            objective_id=f"O{number}",
            defaults={
                "macro_requirement": f"REQ{number // 10} Generic macro",
                "text": f"Generic control objective {number}",
                "assessment_mode": "Control",
            },
        )


class ProjectDimensionsTest(TestCase):
    """CAT-01: what "for this project" filters the catalogue by.

    The dimensions of the project's *selected* objectives, and nothing at all
    when it has selected none yet — the pilot's rule, which the catalogue reads
    as "show everything" rather than "show nothing".
    """

    async def _local_objective(self, project, code, macro, text="An objective"):
        return await ControlObjective.objects.acreate(
            project=project, objective_id=code, macro_requirement=macro,
            text=text, assessment_mode="Control",
        )

    async def test_the_dimensions_come_from_the_selected_objectives(self):
        project = await _project()
        await self._local_objective(project, "CX1", "REQ2 Technical Robustness and Safety")
        await self._local_objective(project, "CX2", "REQ9 Risk Management")
        await ObjectiveProfile.objects.acreate(
            project=project, name="", objectives=["CX1", "CX2"],
        )
        body = (
            await co_client.get(f"/{project.pid}/control-objectives/dimensions")
        ).json()
        self.assertEqual(body["objectives"], ["CX1", "CX2"])
        # one slug per dimension, in requirement order
        self.assertEqual(body["dimensions"], ["technical-robustness-safety", "risk-management"])

    async def test_a_project_that_selected_nothing_gets_no_dimensions(self):
        project = await _project()
        body = (
            await co_client.get(f"/{project.pid}/control-objectives/dimensions")
        ).json()
        self.assertEqual(body, {"objectives": [], "dimensions": []})

    async def test_an_objective_of_the_project_s_own_carries_no_dimension(self):
        project = await _project()
        await self._local_objective(project, "CX1", "CX1 Our own theme")
        await ObjectiveProfile.objects.acreate(
            project=project, name="", objectives=["CX1"],
        )
        body = (
            await co_client.get(f"/{project.pid}/control-objectives/dimensions")
        ).json()
        self.assertEqual(body["objectives"], ["CX1"])
        self.assertEqual(body["dimensions"], [])

    async def test_a_project_that_only_flagged_priorities_is_still_read(self):
        project = await _project()
        await self._local_objective(project, "CX1", "REQ4 Transparency")
        await ObjectiveProfile.objects.acreate(
            project=project, name="", objectives=[], key_objectives=["CX1"],
        )
        body = (
            await co_client.get(f"/{project.pid}/control-objectives/dimensions")
        ).json()
        self.assertEqual(body["dimensions"], ["transparency"])

    async def test_a_stranger_gets_404(self):
        project = await Project.objects.acreate(
            name="co-stranger", description="", status="Ready"
        )
        resp = await co_client.get(f"/{project.pid}/control-objectives/dimensions")
        self.assertEqual(resp.status_code, 404)


class ControlObjectivesRouterTest(TestCase):

    async def test_import_risks_from_card(self):
        project = await _project()
        await Qualification.objects.acreate(
            project=project,
            status="completed",
            system_card={},
            answers={
                "__blocks": {
                    "risks": [
                        {"risk": "Wrongly rejected", "source": "stale data",
                         "consequence": "loan refused"},
                        {"risk": "Group divergence", "source": "biased features",
                         "consequence": "discriminatory"},
                    ]
                }
            },
        )
        resp = await co_client.post(f"/{project.pid}/control-objectives/risks/from-card")
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertEqual(
            [r["name"] for r in body["created"]],
            ["Wrongly rejected", "Group divergence"],
        )
        self.assertEqual(body["updated"], [])
        # the AIRO chain (source → consequence) is kept, the risk is unrated
        self.assertEqual(body["created"][0]["airo_text"], "stale data → loan refused")
        self.assertEqual(body["created"][0]["rating"], 0)

    async def test_import_risks_updates_existing_without_clearing_ratings(self):
        from aisc_control_objectives.models import Risk as RiskRow

        project = await _project()
        await _seed_objectives()
        existing = await RiskRow.objects.acreate(
            project=project, position=1, name="Wrongly rejected",
            impact=4, likelihood=3, airo_text="old chain",
        )
        await Qualification.objects.acreate(
            project=project,
            status="completed",
            system_card={},
            answers={
                "__blocks": {
                    "risks": [
                        {"risk": "Wrongly rejected", "source": "new data",
                         "consequence": "loan refused"},
                        {"risk": "New risk", "source": "s",
                         "consequence": "c"},
                    ]
                }
            },
        )
        resp = await co_client.post(f"/{project.pid}/control-objectives/risks/from-card")
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertEqual([r["name"] for r in body["updated"]], ["Wrongly rejected"])
        self.assertEqual([r["name"] for r in body["created"]], ["New risk"])
        updated = body["updated"][0]
        # the card text is refreshed, but the person's rating is preserved
        self.assertEqual(updated["airo_text"], "new data → loan refused")
        self.assertEqual(updated["impact"], 4)
        self.assertEqual(updated["likelihood"], 3)
        self.assertEqual(updated["rating"], 12)
        row = await RiskRow.objects.aget(pk=existing.pk)
        self.assertEqual(row.impact, 4)
        self.assertEqual(row.likelihood, 3)

    async def test_import_risks_from_card_missing_card_404(self):
        project = await _project()
        resp = await co_client.post(f"/{project.pid}/control-objectives/risks/from-card")
        self.assertEqual(resp.status_code, 404)

    async def test_catalogue_is_seeded_and_readable(self):
        project = await _project()
        await _seed_objectives()
        resp = await co_client.get(f"/{project.pid}/control-objectives/catalogue")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.json()), 50)
        self.assertEqual(resp.json()[0]["id"], "O1")

    async def test_seed_migration_provides_the_real_catalogue(self):
        """The 0032 migration loads the AI-Act O1..O50 objectives from the CSV:
        the page expects ~50 default rows with their real texts."""
        project = await _project()
        resp = await co_client.get(f"/{project.pid}/control-objectives/catalogue")
        self.assertEqual(resp.status_code, 200)
        by_id = {o["id"]: o for o in resp.json()}
        self.assertEqual(len(by_id), 50)
        self.assertTrue(
            by_id["O1"]["text"].startswith("A qualified operator"),
            by_id["O1"]["text"],
        )
        self.assertEqual(by_id["O50"]["id"], "O50")

    async def test_catalogue_add_edit_delete_project_row(self):
        await _seed_objectives()
        project = await _project()
        other = await Project.objects.acreate(name="other", description="", status="Ready")
        await ProjectMember.objects.acreate(
            project=other, subject="development", role=MembershipRole.Owner,
        )
        payload = {
            "objective_id": "CX-1",
            "macro_requirement": "REQX Custom control",
            "text": "A custom objective",
            "assessment_mode": "Control",
        }
        added = (await co_client.post(
            f"/{project.pid}/control-objectives/catalogue", json=payload,
        ))
        self.assertEqual(added.status_code, 200, added.text)
        self.assertEqual(added.json()["id"], "CX-1")
        self.assertTrue(added.json()["custom"])
        seeded_row = (await co_client.get(f"/{project.pid}/control-objectives/catalogue")).json()
        self.assertFalse([o for o in seeded_row if o["id"] == "O1"][0]["custom"])

        mine = (await co_client.get(f"/{project.pid}/control-objectives/catalogue")).json()
        self.assertIn("CX-1", [o["id"] for o in mine])
        theirs = (await co_client.get(f"/{other.pid}/control-objectives/catalogue")).json()
        self.assertNotIn("CX-1", [o["id"] for o in theirs])

        # A local code is per project: another project may keep the same one...
        theirs_add = await co_client.post(
            f"/{other.pid}/control-objectives/catalogue", json=payload,
        )
        self.assertEqual(theirs_add.status_code, 200, theirs_add.text)
        # ...but it may not repeat inside the project, nor shadow the catalogue.
        dup = await co_client.post(
            f"/{project.pid}/control-objectives/catalogue", json=payload,
        )
        self.assertEqual(dup.status_code, 409)
        shadow = await co_client.post(
            f"/{project.pid}/control-objectives/catalogue",
            json={**payload, "objective_id": "O1"},
        )
        self.assertEqual(shadow.status_code, 409)

        bad = await co_client.post(
            f"/{project.pid}/control-objectives/catalogue", json={"objective_id": "CX-2"},
        )
        self.assertEqual(bad.status_code, 422)
        blank = await co_client.post(
            f"/{project.pid}/control-objectives/catalogue",
            json={**payload, "objective_id": "CX-2", "text": "  "},
        )
        self.assertEqual(blank.status_code, 400)

        edited = (await co_client.put(
            f"/{project.pid}/control-objectives/catalogue/CX-1",
            json={**payload, "text": "Updated custom objective"},
        )).json()
        self.assertEqual(edited["text"], "Updated custom objective")
        matrix = (await co_client.get(f"/{project.pid}/control-objectives/matrix")).json()
        self.assertIn("CX-1", [p["objective_id"] for p in matrix["priorities"]])

        seeded = await co_client.put(
            f"/{project.pid}/control-objectives/catalogue/O1",
            json={**payload, "objective_id": "O1"},
        )
        self.assertEqual(seeded.status_code, 403)
        missing = await co_client.delete(
            f"/{project.pid}/control-objectives/catalogue/NOPE",
        )
        self.assertEqual(missing.status_code, 404)

        gone = await co_client.delete(
            f"/{project.pid}/control-objectives/catalogue/CX-1",
        )
        self.assertEqual(gone.status_code, 204)
        mine = (await co_client.get(f"/{project.pid}/control-objectives/catalogue")).json()
        self.assertNotIn("CX-1", [o["id"] for o in mine])

    async def test_risk_crud_rating_mapping(self):
        await _seed_objectives()
        project = await _project()

        # the register is seeded from the AI card, not a manual add form
        risk = await _risk(project, "Poisoned training data")
        self.assertEqual(0, risk.impact or 0)   # unrated

        rated = (await co_client.patch(
            f"/{project.pid}/control-objectives/risks/{risk.pid}/rating",
            json={"impact": 3, "likelihood": 4},
        )).json()
        self.assertEqual(rated["rating"], 12)   # 3 * 4 = 12 -> High band

        # map it to an objective (manual)
        mapped = (await co_client.post(
            f"/{project.pid}/control-objectives/risks/{risk.pid}/mappings",
            json={"objective_id": "O3",
                  "quote": "training data", "rationale": "mitigates poisoning"},
        )).json()
        self.assertEqual(mapped["objective_id"], "O3")
        self.assertEqual(mapped["source"], "person")

    async def test_risk_versions_append_only(self):
        project = await _project()
        risk = await _risk(project, "Poisoned training data")

        # an import-only risk has no revision yet: a version only exists for an
        # assessment, so the first one is v1 (rated), never an unrated v1 + v2
        versions = (await co_client.get(
            f"/{project.pid}/control-objectives/risks/{risk.pid}/versions")).json()
        self.assertEqual(versions, [])

        # the first rating creates v1
        (await co_client.patch(
            f"/{project.pid}/control-objectives/risks/{risk.pid}/rating",
            json={"impact": 3, "likelihood": 4},
        )).json()
        versions = (await co_client.get(
            f"/{project.pid}/control-objectives/risks/{risk.pid}/versions")).json()
        self.assertEqual([v["number"] for v in versions], [1])
        self.assertEqual(versions[-1]["rating"], 12)
        self.assertEqual(versions[-1]["name"], "Poisoned training data")

        # a no-op re-rate (same impact/likelihood/comment) does NOT create a version
        (await co_client.patch(
            f"/{project.pid}/control-objectives/risks/{risk.pid}/rating",
            json={"impact": 3, "likelihood": 4, "comment": ""},
        )).json()
        versions = (await co_client.get(
            f"/{project.pid}/control-objectives/risks/{risk.pid}/versions")).json()
        self.assertEqual([v["number"] for v in versions], [1])

        # reading the old number returns the assessment as it was
        v1 = (await co_client.get(
            f"/{project.pid}/control-objectives/risks/{risk.pid}/versions/1")).json()
        self.assertEqual(v1["rating"], 12)
        self.assertEqual(v1["risk_pid"], str(risk.pid))
        missing = await co_client.get(
            f"/{project.pid}/control-objectives/risks/{risk.pid}/versions/2")
        self.assertEqual(missing.status_code, 404)


    async def test_matrix_scores_and_ranks(self):
        await _seed_objectives()
        project = await _project()
        risks = [
            await _risk(project, f"risk {i}")
            for i in (1, 2)
        ]
        await co_client.patch(
            f"/{project.pid}/control-objectives/risks/{risks[0].pid}/rating",
            json={"impact": 5, "likelihood": 5})   # 25 critical
        await co_client.patch(
            f"/{project.pid}/control-objectives/risks/{risks[1].pid}/rating",
            json={"impact": 2, "likelihood": 2})   # 4 low
        await co_client.post(f"/{project.pid}/control-objectives/risks/{risks[0].pid}/mappings",
                             json={"objective_id": "O1"})
        await co_client.post(f"/{project.pid}/control-objectives/risks/{risks[1].pid}/mappings",
                             json={"objective_id": "O1"})

        matrix = (await co_client.get(f"/{project.pid}/control-objectives/matrix")).json()
        priorities = {p["objective_id"]: p for p in matrix["priorities"]}
        self.assertEqual(len(priorities), 50)
        o1 = priorities["O1"]
        self.assertEqual(o1["score"], 29.0)       # 25 + 4
        self.assertEqual(o1["top_rating"], 25)    # the critical one
        self.assertTrue(o1["key_default"])         # driven by a critical risk
        self.assertTrue(o1["key"])
        # every objective is present, with O1 ranked first
        self.assertEqual(matrix["priorities"][0]["objective_id"], "O1")

    async def test_profile_set_and_read(self):
        await _seed_objectives()
        project = await _project()
        resp = await co_client.put(f"/{project.pid}/control-objectives/profile", json={
            "name": "default", "objectives": ["O1", "O2"], "key_objectives": ["O1"],
        })
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["key_objectives"], ["O1"])

        read = (await co_client.get(f"/{project.pid}/control-objectives/profile")).json()
        self.assertEqual(read["name"], "default")

    async def test_assessor_key_choice_overrides_in_matrix(self):
        await _seed_objectives()
        project = await _project()
        risk = await _risk(project, "r")
        await co_client.patch(
            f"/{project.pid}/control-objectives/risks/{risk.pid}/rating",
            json={"impact": 3, "likelihood": 3})   # 9 medium
        await co_client.post(f"/{project.pid}/control-objectives/risks/{risk.pid}/mappings",
                             json={"objective_id": "O9"})
        # force O9 key even though its risk is only Medium
        await co_client.put(f"/{project.pid}/control-objectives/profile", json={
            "name": "p", "objectives": [], "key_objectives": ["O9"],
        })
        matrix = (await co_client.get(f"/{project.pid}/control-objectives/matrix")).json()
        priorities = {p["objective_id"]: p for p in matrix["priorities"]}
        self.assertTrue(priorities["O9"]["key"])
        self.assertFalse(priorities["O9"]["key_default"])

    async def test_unticking_a_default_key_sticks_and_undo_restores_it(self):
        """The assessor may untick a default priority objective, and undo it."""
        await _seed_objectives()
        project = await _project()
        risk = await _risk(project, "critical risk")
        await co_client.patch(
            f"/{project.pid}/control-objectives/risks/{risk.pid}/rating",
            json={"impact": 5, "likelihood": 5})   # 25 critical -> O1 key by default
        await co_client.post(f"/{project.pid}/control-objectives/risks/{risk.pid}/mappings",
                             json={"objective_id": "O1"})

        matrix = (await co_client.get(f"/{project.pid}/control-objectives/matrix")).json()
        o1 = {p["objective_id"]: p for p in matrix["priorities"]}["O1"]
        self.assertTrue(o1["key"])
        self.assertTrue(o1["key_default"])

        # the assessor chooses a set that leaves O1 out: O1 is no longer key
        await co_client.put(f"/{project.pid}/control-objectives/profile", json={
            "name": "p", "objectives": [], "key_objectives": ["O2"],
        })
        matrix = (await co_client.get(f"/{project.pid}/control-objectives/matrix")).json()
        o1 = {p["objective_id"]: p for p in matrix["priorities"]}["O1"]
        self.assertFalse(o1["key"])
        self.assertTrue(o1["key_default"])   # still the default, just overridden

        # undo the choices: the computed defaults apply again
        undone = await co_client.delete(f"/{project.pid}/control-objectives/profile/keys")
        self.assertEqual(undone.status_code, 200, undone.text)
        self.assertEqual(undone.json()["key_objectives"], [])
        matrix = (await co_client.get(f"/{project.pid}/control-objectives/matrix")).json()
        o1 = {p["objective_id"]: p for p in matrix["priorities"]}["O1"]
        self.assertTrue(o1["key"])

    async def test_rating_bounds_and_rationale_length(self):
        """Impact/likelihood are 1..5; the rationale is at most 1000 characters."""
        project = await _project()
        risk = await _risk(project, "r")
        too_big = await co_client.patch(
            f"/{project.pid}/control-objectives/risks/{risk.pid}/rating",
            json={"impact": 6, "likelihood": 1},
        )
        self.assertEqual(too_big.status_code, 400)
        too_small = await co_client.patch(
            f"/{project.pid}/control-objectives/risks/{risk.pid}/rating",
            json={"impact": 1, "likelihood": 0},
        )
        self.assertEqual(too_small.status_code, 400)
        long_comment = await co_client.patch(
            f"/{project.pid}/control-objectives/risks/{risk.pid}/rating",
            json={"impact": 1, "likelihood": 1, "comment": "x" * 1001},
        )
        self.assertEqual(long_comment.status_code, 400)
        ok = await co_client.patch(
            f"/{project.pid}/control-objectives/risks/{risk.pid}/rating",
            json={"impact": 5, "likelihood": 5, "comment": "x" * 1000},
        )
        self.assertEqual(ok.status_code, 200, ok.text)

    async def test_manual_score_and_likelihood_override_the_matrix(self):
        await _seed_objectives()
        project = await _project()
        resp = await co_client.put(f"/{project.pid}/control-objectives/ratings", json={
            "ratings": {"O1": {"score": 4, "likelihood": 5, "comment": "reviewed with the vendor"}},
        })
        self.assertEqual(resp.status_code, 200, resp.text)

        matrix = (await co_client.get(f"/{project.pid}/control-objectives/matrix")).json()
        o1 = {p["objective_id"]: p for p in matrix["priorities"]}["O1"]
        self.assertEqual(o1["score"], 4.0)
        self.assertEqual(o1["likelihood"], 5)
        self.assertEqual(o1["comment"], "reviewed with the vendor")

        # out-of-range manual scores are clamped to the 0-5 scale
        resp = await co_client.put(f"/{project.pid}/control-objectives/ratings", json={
            "ratings": {"O1": {"score": 42}},
        })
        self.assertEqual(resp.status_code, 200, resp.text)
        matrix = (await co_client.get(f"/{project.pid}/control-objectives/matrix")).json()
        o1 = {p["objective_id"]: p for p in matrix["priorities"]}["O1"]
        self.assertEqual(o1["score"], 5.0)

        # objectives without a manual entry keep their computed values
        o2 = {p["objective_id"]: p for p in matrix["priorities"]}["O2"]
        self.assertEqual(o2["score"], 0.0)
        self.assertIsNone(o2["likelihood"])

    async def test_stranger_cannot_read(self):
        project = await Project.objects.acreate(name="x", description="", status="Ready")
        resp = await co_client.get(f"/{project.pid}/control-objectives/catalogue")
        self.assertEqual(resp.status_code, 404)
