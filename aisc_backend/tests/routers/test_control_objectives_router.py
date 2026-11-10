"""
Router tests for control objectives: the risk register, the risk-objective
mapping, and the matrix computed on read — all project-scoped (a stranger gets
404, a member reads, an editor writes).
"""
from django.test import TestCase
from ninja.testing import TestAsyncClient

from aisc_backend.models.control_objective import ControlObjective
from aisc_backend.models.project import Project
from aisc_backend.models.project_member import MembershipRole, ProjectMember
from aisc_backend.models.qualification import Qualification
from aisc_backend.routers.control_objectives import router as co_router

co_client = TestAsyncClient(co_router)


async def _project():
    project = await Project.objects.acreate(name="co-project", description="", status="Ready")
    await ProjectMember.objects.acreate(
        project=project, subject="development", role=MembershipRole.Owner,
    )
    return project


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
        from aisc_backend.models.control_objective import Risk as RiskRow

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

        dup = await co_client.post(
            f"/{other.pid}/control-objectives/catalogue", json=payload,
        )
        self.assertEqual(dup.status_code, 409)

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

        # add a risk, then rate it
        risk = (await co_client.post(f"/{project.pid}/control-objectives/risks", json={
            "name": "Poisoned training data",
        })).json()
        self.assertEqual(risk["rating"], 0)   # unrated

        rated = (await co_client.patch(
            f"/{project.pid}/control-objectives/risks/{risk['pid']}/rating",
            json={"impact": 3, "likelihood": 4},
        )).json()
        self.assertEqual(rated["rating"], 12)   # 3 * 4 = 12 -> High band

        # map it to an objective (manual)
        mapped = (await co_client.post(
            f"/{project.pid}/control-objectives/risks/{risk['pid']}/mappings",
            json={"objective_id": "O3",
                  "quote": "training data", "rationale": "mitigates poisoning"},
        )).json()
        self.assertEqual(mapped["objective_id"], "O3")
        self.assertEqual(mapped["source"], "person")

    async def test_risk_versions_append_only(self):
        project = await _project()
        risk = (await co_client.post(f"/{project.pid}/control-objectives/risks", json={
            "name": "Poisoned training data",
        })).json()

        # adding a risk records v1 (its initial, unrated state)
        versions = (await co_client.get(
            f"/{project.pid}/control-objectives/risks/{risk['pid']}/versions")).json()
        self.assertEqual([v["number"] for v in versions], [1])
        self.assertEqual(versions[0]["rating"], 0)

        # rating creates v2 with the new assessment
        (await co_client.patch(
            f"/{project.pid}/control-objectives/risks/{risk['pid']}/rating",
            json={"impact": 3, "likelihood": 4},
        )).json()
        versions = (await co_client.get(
            f"/{project.pid}/control-objectives/risks/{risk['pid']}/versions")).json()
        self.assertEqual([v["number"] for v in versions], [1, 2])
        self.assertEqual(versions[-1]["rating"], 12)
        self.assertEqual(versions[-1]["name"], "Poisoned training data")

        # a no-op re-rate (same impact/likelihood/comment) does NOT create a version
        (await co_client.patch(
            f"/{project.pid}/control-objectives/risks/{risk['pid']}/rating",
            json={"impact": 3, "likelihood": 4, "comment": ""},
        )).json()
        versions = (await co_client.get(
            f"/{project.pid}/control-objectives/risks/{risk['pid']}/versions")).json()
        self.assertEqual([v["number"] for v in versions], [1, 2])

        # reading an old number returns the assessment as it was
        v1 = (await co_client.get(
            f"/{project.pid}/control-objectives/risks/{risk['pid']}/versions/1")).json()
        self.assertEqual(v1["rating"], 0)
        self.assertEqual(v1["risk_pid"], risk["pid"])
        missing = await co_client.get(
            f"/{project.pid}/control-objectives/risks/{risk['pid']}/versions/99")
        self.assertEqual(missing.status_code, 404)


    async def test_matrix_scores_and_ranks(self):
        await _seed_objectives()
        project = await _project()
        risks = [
            (await co_client.post(f"/{project.pid}/control-objectives/risks",
                                  json={"name": f"risk {i}"})).json()
            for i in (1, 2)
        ]
        await co_client.patch(
            f"/{project.pid}/control-objectives/risks/{risks[0]['pid']}/rating",
            json={"impact": 5, "likelihood": 5})   # 25 critical
        await co_client.patch(
            f"/{project.pid}/control-objectives/risks/{risks[1]['pid']}/rating",
            json={"impact": 2, "likelihood": 2})   # 4 low
        await co_client.post(f"/{project.pid}/control-objectives/risks/{risks[0]['pid']}/mappings",
                             json={"objective_id": "O1"})
        await co_client.post(f"/{project.pid}/control-objectives/risks/{risks[1]['pid']}/mappings",
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
        risk = (await co_client.post(f"/{project.pid}/control-objectives/risks",
                                     json={"name": "r"})).json()
        await co_client.patch(
            f"/{project.pid}/control-objectives/risks/{risk['pid']}/rating",
            json={"impact": 3, "likelihood": 3})   # 9 medium
        await co_client.post(f"/{project.pid}/control-objectives/risks/{risk['pid']}/mappings",
                             json={"objective_id": "O9"})
        # force O9 key even though its risk is only Medium
        await co_client.put(f"/{project.pid}/control-objectives/profile", json={
            "name": "p", "objectives": [], "key_objectives": ["O9"],
        })
        matrix = (await co_client.get(f"/{project.pid}/control-objectives/matrix")).json()
        priorities = {p["objective_id"]: p for p in matrix["priorities"]}
        self.assertTrue(priorities["O9"]["key"])
        self.assertFalse(priorities["O9"]["key_default"])

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
