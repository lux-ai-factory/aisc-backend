"""
Tests for services.controls_catalogue.sync_controls_catalogue.

Contract-first: the hosted catalogue calls (_get_json) are stubbed. Covers:
sources/templates/questions upserted, a re-run is idempotent, a question
edit updates IN PLACE — never delete+recreate, because saved answers (FK with
CASCADE) must keep pointing at the same question row — plus the read-through
surface: an empty DB lists and installs straight from the hosted catalogue.
"""
from unittest import mock

from django.test import TestCase, override_settings
from ninja.testing import TestAsyncClient

from aisc_backend.models.control import (
    ControlQuestion,
    ControlSource,
    ControlTemplate,
    ProjectChecklist,
)
from aisc_backend.models.project import Project
from aisc_backend.models.project_member import MembershipRole, ProjectMember
from aisc_backend.routers.catalogue import router as catalogue_router
from aisc_backend.services import controls_catalogue

catalogue_client = TestAsyncClient(catalogue_router)

TOOLS = [
    {"id": 1, "slug": "risk-management-checklist", "name": "Risk Management Checklist"},
    {"id": 2, "slug": "accuracy-checklist", "name": "Accuracy Checklist"},
]


def _export(slug, title, q1="question one", source_name="AESIA"):
    return {
        "meta": {
            "catalogueId": slug,
            "title": title,
            "sourceName": source_name,
            "sourceUrl": "https://aesia.digital.gob.es/en/guides",
            "controlTopic": "Risk Management" if slug.startswith("risk") else "Accuracy",
            "description": f"{title} description",
            "countryIds": ["ES"],
            "regulationIds": ["ai-act"],
            "sourceUpdatedAt": None,
        },
        "questions": [
            {"text": q1, "article": "Article 9", "category": "MG1"},
            {"text": "question two", "article": "Article 9", "category": "MG1"},
        ],
    }


def _default_exports():
    return {
        "risk-management-checklist": _export("risk-management-checklist", "Risk Management Checklist"),
        "accuracy-checklist": _export("accuracy-checklist", "Accuracy Checklist"),
    }


def _stubbed_get_json(exports_by_slug, tools=TOOLS):
    """An async _get_json keyed on URL: the sync fetches concurrently (gather
    reorders calls), so tests must not rely on call order."""

    async def _fake(url):
        if url.endswith("/tool/?detailed=true"):
            return tools
        slug = url.rsplit("/control/", 1)[1].rsplit("/export", 1)[0]
        if slug in exports_by_slug:
            return exports_by_slug[slug]
        raise controls_catalogue.CatalogueUnavailable(f"no export for {slug}")

    return _fake


class ControlsCatalogueSyncTest(TestCase):

    async def _run(self, exports_by_slug, tools=TOOLS):
        with mock.patch.object(
            controls_catalogue, "_get_json",
            new_callable=mock.AsyncMock,
            side_effect=_stubbed_get_json(exports_by_slug, tools),
        ):
            return await controls_catalogue.sync_controls_catalogue()

    async def test_sync_creates_sources_templates_questions(self):
        summary = await self._run(_default_exports())

        self.assertEqual(summary["templates"], 2)
        self.assertEqual(summary["questions_created"], 4)
        self.assertEqual(summary["errors"], [])
        self.assertEqual(await ControlSource.objects.acount(), 1)     # both AESIA
        self.assertEqual(await ControlTemplate.objects.acount(), 2)
        self.assertEqual(await ControlQuestion.objects.acount(), 4)

        template = await ControlTemplate.objects.select_related("source").aget(
            catalogue_id="risk-management-checklist"
        )
        self.assertEqual(template.control_topic, "Risk Management")
        self.assertEqual(template.regulation_ids, ["ai-act"])
        self.assertEqual(template.source.name, "AESIA")

    async def test_sync_is_idempotent(self):
        await self._run(_default_exports())
        summary = await self._run(_default_exports())

        self.assertEqual(summary["templates"], 0)
        self.assertEqual(summary["questions_created"], 0)
        self.assertEqual(summary["questions_updated"], 4)
        self.assertEqual(await ControlTemplate.objects.acount(), 2)
        self.assertEqual(await ControlQuestion.objects.acount(), 4)

    async def test_question_edit_updates_in_place_and_keeps_pk(self):
        await self._run(_default_exports())
        template = await ControlTemplate.objects.aget(catalogue_id="risk-management-checklist")
        before = await ControlQuestion.objects.filter(template=template, order=1).afirst()
        before_pk = before.pk

        exports = {
            "risk-management-checklist": _export(
                "risk-management-checklist", "Risk Management Checklist", q1="edited question",
            ),
            "accuracy-checklist": _export("accuracy-checklist", "Accuracy Checklist"),
        }
        summary = await self._run(exports)

        self.assertEqual(summary["questions_updated"], 4)
        # still the same row: the CASCADE from saved answers must not fire
        after = await ControlQuestion.objects.filter(template=template, order=1).afirst()
        self.assertEqual(after.pk, before_pk)
        self.assertEqual(after.text, "edited question")

    async def test_one_bad_checklist_does_not_stop_the_rest(self):
        exports = {
            "risk-management-checklist": "not-a-dict",  # bad export
            "accuracy-checklist": _export("accuracy-checklist", "Accuracy Checklist"),
        }
        summary = await self._run(exports)

        self.assertEqual(len(summary["errors"]), 1)
        self.assertEqual(await ControlTemplate.objects.acount(), 1)
        template = await ControlTemplate.objects.aget(catalogue_id="accuracy-checklist")
        self.assertEqual(template.name, "Accuracy Checklist")

    async def test_non_control_items_are_skipped(self):
        tools = [
            {"id": 1, "slug": "risk-management-checklist", "name": "Risk Management Checklist",
             "tags": [{"section": "type", "slug": "control"}]},
            {"id": 2, "slug": "torchmetrics", "name": "TorchMetrics",
             "tags": [{"section": "type", "slug": "test"}]},
        ]
        exports = {
            "risk-management-checklist": _export("risk-management-checklist", "Risk Management Checklist"),
        }
        summary = await self._run(exports, tools=tools)

        # only the control was synced; the test item never fetched an export
        self.assertEqual(summary["errors"], [])
        self.assertEqual(await ControlTemplate.objects.acount(), 1)
        template = await ControlTemplate.objects.aget(catalogue_id="risk-management-checklist")
        self.assertEqual(template.name, "Risk Management Checklist")


class CatalogueReadThroughTest(TestCase):
    """GET /catalogue lists online (nothing stored), install materialises only
    what a project actually installs, export proxies the online package."""

    async def _owned_project(self, name="catalogue-project"):
        project = await Project.objects.acreate(name=name, description="", status="Ready")
        await ProjectMember.objects.acreate(
            project=project, subject="development", role=MembershipRole.Owner,
        )
        return project

    async def test_catalogue_lists_online_without_storing(self):
        controls_catalogue._list_cache = None
        tools = [
            {"id": 1, "slug": "risk-management-checklist", "name": "Risk Management Checklist",
             "metadata": {"provider": "AESIA", "control_topic": "Risk Management"},
             "questions": [{"text": "q1", "article": "Article 9"}, {"text": "q2"}]},
            {"id": 2, "slug": "accuracy-checklist", "name": "Accuracy Checklist",
             "metadata": {"provider": "AESIA", "control_topic": "Accuracy"}},
        ]
        with mock.patch.object(
            controls_catalogue, "_get_json",
            new_callable=mock.AsyncMock,
            side_effect=[tools],  # only the list is fetched; exports are never pulled
        ):
            resp = await catalogue_client.get("/catalogue")

        self.assertEqual(resp.status_code, 200)
        controls = {c["catalogue_id"]: c for c in resp.json()["controls"]}
        self.assertEqual(set(controls), {"risk-management-checklist", "accuracy-checklist"})
        # the list is METADATA only: title/source/topic and the count, no questions
        self.assertEqual(controls["risk-management-checklist"]["title"], "Risk Management Checklist")
        self.assertEqual(controls["risk-management-checklist"]["source"], "AESIA")
        self.assertEqual(controls["risk-management-checklist"]["question_count"], 2)
        self.assertNotIn("questions", controls["risk-management-checklist"])
        self.assertEqual(controls["accuracy-checklist"]["question_count"], 0)
        # listing must NOT store anything: rows exist only after an install
        self.assertEqual(await ControlTemplate.objects.acount(), 0)
        self.assertEqual(await ControlSource.objects.acount(), 0)

    async def test_list_results_are_cached(self):
        controls_catalogue._list_cache = None
        with mock.patch.object(
            controls_catalogue, "_get_json",
            new_callable=mock.AsyncMock,
            side_effect=[TOOLS],
        ) as get_json:
            first = await controls_catalogue.list_controls()
            second = await controls_catalogue.list_controls()
        self.assertEqual(first, second)
        self.assertEqual(get_json.call_count, 1)  # cache hit, no second fetch

    @override_settings(APP_CATALOG_URL="https://sandboxconfigurator.aifactory.lu/catalogue")
    async def test_catalogue_carries_the_published_site_url(self):
        """CAT-01: the product links the independently published catalogue."""
        controls_catalogue._list_cache = None
        with mock.patch.object(
            controls_catalogue, "_get_json",
            new_callable=mock.AsyncMock,
            side_effect=[TOOLS],
        ):
            body = (await catalogue_client.get("/catalogue")).json()
        self.assertEqual(
            body["app_catalog_url"], "https://sandboxconfigurator.aifactory.lu/catalogue"
        )

    async def test_kind_narrows_to_one_catalogue_kind(self):
        """One combined UI can ask for tests or checklists only."""
        controls_catalogue._list_cache = None
        with mock.patch.object(
            controls_catalogue, "_get_json",
            new_callable=mock.AsyncMock,
            side_effect=[TOOLS],
        ) as get_json:
            only_controls = (await catalogue_client.get("/catalogue?kind=control")).json()
        self.assertTrue(only_controls["controls"])
        self.assertEqual(only_controls["plugins"], [])

        get_json.reset_mock()
        only_plugins = (await catalogue_client.get("/catalogue?kind=plugin")).json()
        self.assertEqual(only_plugins["controls"], [])
        get_json.assert_not_called()  # no checklist kind asked for: no fetch at all

        bad = await catalogue_client.get("/catalogue?kind=nonsense")
        self.assertEqual(bad.status_code, 400)

    async def test_install_is_project_scoped(self):
        """A stranger gets 404 and a viewer 403 on the install route."""
        stranger = await Project.objects.acreate(name="s", description="", status="Ready")
        resp = await catalogue_client.post(
            f"/projects/{stranger.pid}/catalogue/installs"
            f"?catalogue_id=accuracy-checklist&kind=control",
        )
        self.assertEqual(resp.status_code, 404)

        viewer_project = await Project.objects.acreate(
            name="v", description="", status="Ready"
        )
        await ProjectMember.objects.acreate(
            project=viewer_project, subject="development", role=MembershipRole.Viewer,
        )
        resp = await catalogue_client.post(
            f"/projects/{viewer_project.pid}/catalogue/installs"
            f"?catalogue_id=accuracy-checklist&kind=control",
        )
        self.assertEqual(resp.status_code, 403)

    async def test_install_materialises_only_the_installed_control(self):
        project = await self._owned_project()
        with mock.patch.object(
            controls_catalogue, "_get_json",
            new_callable=mock.AsyncMock,
            side_effect=[_export("accuracy-checklist", "Accuracy Checklist")],
        ):
            resp = await catalogue_client.post(
                f"/projects/{project.pid}/catalogue/installs"
                f"?catalogue_id=accuracy-checklist&kind=control",
            )

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["status"], "installed")
        self.assertEqual(await ControlTemplate.objects.acount(), 1)
        self.assertEqual(await ControlQuestion.objects.acount(), 2)
        self.assertEqual(await ProjectChecklist.objects.filter(project=project).acount(), 1)

    async def test_export_proxies_online(self):
        project = await self._owned_project()
        with mock.patch.object(
            controls_catalogue, "_get_json",
            new_callable=mock.AsyncMock,
            side_effect=[_export("accuracy-checklist", "Accuracy Checklist")],
        ):
            resp = await catalogue_client.get(
                f"/projects/{project.pid}/catalogue/control/accuracy-checklist/export"
            )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["meta"]["catalogueId"], "accuracy-checklist")
        self.assertTrue(resp.json()["questions"])
        # export must not store either
        self.assertEqual(await ControlTemplate.objects.acount(), 0)
