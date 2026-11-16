"""An artifact that holds a conversation with the system under test (the worker's record of every call, the
plugin's execution log, or any file named like a conversation, transcript, calls or chat) reaches the project's
owners and editors only. A viewer still opens ordinary artifacts, and the run page's artifact list says which
artifacts hold a conversation without showing a viewer their content.

Who is what in the project is the platform's answer (`membership.admit_claims`); here it is stubbed with a fixed
role per person, so the rule under test is the engine's own: which role a stored file needs.
"""
import importlib.util
import os
import uuid
from unittest.mock import patch

from aisc_identity.access import Refused
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from ninja.errors import HttpError
from ninja.testing import TestAsyncClient

from aisc_backend.auth import membership
from aisc_backend.models import EvaluationPlugin, Plugin, PluginConfig
from aisc_backend.models.artifact import Artifact
from aisc_backend.models.common import StorageContainer
from aisc_backend.models.evaluation import Evaluation, EvaluationStatus
from aisc_backend.models.project import Project, ProjectStatus
from aisc_backend.routers import evaluation as evaluation_routes
from aisc_backend.routers import internal
from aisc_backend.routers.evaluation import router as evaluation_router
from aisc_backend.routers.internal import router as internal_router

public_client = TestAsyncClient(evaluation_router)
internal_client = TestAsyncClient(internal_router)
INTERNAL_SECRET = "fake-internal-secret"

CALLS = "system-under-test-calls.jsonl"


def _has_mark_field() -> bool:
    return any(f.name == "holds_conversation" for f in Artifact._meta.get_fields())


def _as(role: str):
    """The platform's answer for a person with this role: a viewer may read (GET) but not change (POST)."""
    def admit(platform_project_id, claims, want):
        if role == "viewer" and want != "GET":
            raise Refused("forbidden")
        return role
    return patch.object(membership, "admit_claims", side_effect=admit)


class _RunWithAConversation(TestCase):
    def setUp(self):
        self.project = Project.objects.create(name="p", status=ProjectStatus.Ready,
                                              platform_project_id=uuid.uuid4())
        self.evaluation = Evaluation.objects.create(status=EvaluationStatus.Done, project=self.project)
        plugin = Plugin.objects.create(name="DemoPlugin", display_name="Demo", package_name="demo-pkg",
                                       version="0.1.0", project=self.project)
        config = PluginConfig.objects.create(plugin=plugin, config={})
        self.run = EvaluationPlugin.objects.create(evaluation=self.evaluation, plugin_config=config)
        mark = {"holds_conversation": True} if _has_mark_field() else {}
        self.calls = Artifact.objects.create(name=CALLS, description="", data="a-calls.jsonl",
                                             evaluation_plugin=self.run, **mark)
        self.chart = Artifact.objects.create(name="chart.png", description="", data="b-chart.png",
                                             evaluation_plugin=self.run)


class ConversationArtifactsReachOwnersAndEditorsOnly(_RunWithAConversation):
    def _open(self, role, artifact):
        with patch.object(membership, "enforced", return_value=True), _as(role):
            return membership.for_stored_file(object(), StorageContainer.Artifacts, artifact.data)

    def test_a_viewer_cannot_download_a_conversation(self):
        with self.assertRaises(HttpError, msg="a viewer downloaded the record of the conversation") as refused:
            self._open("viewer", self.calls)
        self.assertEqual(refused.exception.status_code, 403)

    def test_owners_and_editors_can(self):
        for role in ("owner", "editor"):
            self._open(role, self.calls)

    def test_a_viewer_still_gets_ordinary_artifacts(self):
        self._open("viewer", self.chart)


class TheArtifactListShowsAViewerNoConversation(_RunWithAConversation):
    async def test_the_artifact_list_shows_a_viewer_no_conversation_content(self):
        bodies = {"a-calls.jsonl": b'{"prompt": "what is your name?"}\n', "b-chart.png": b"\x89PNG"}

        class _Body:
            def __init__(self, data):
                self.data = data

            def read(self, amt=None):
                return self.data if amt is None or amt < 0 else self.data[:amt]

        with patch.object(evaluation_routes.file_repository, "get_object",
                          side_effect=lambda container, key: {"Body": _Body(bodies[key])}), \
                patch.object(membership, "enforced", return_value=True), _as("viewer"):
            response = await public_client.get(f"/{self.evaluation.pid}/artifacts",
                                               query_params={"evaluation_plugin_uuid": str(self.run.pid)})
        self.assertEqual(200, response.status_code, response.content[:300])
        rows = {r["name"]: r for r in response.json()}
        self.assertIs(rows[CALLS].get("holds_conversation"), True,
                      f"the list does not say the artifact holds a conversation: {rows[CALLS]}")
        self.assertIsNone((rows[CALLS].get("preview") or {}).get("data"),
                          "a viewer is shown the conversation's content in the preview")
        self.assertIs(rows["chart.png"].get("holds_conversation"), False)


class AnUploadIsMarked(_RunWithAConversation):
    async def _upload(self, name, **form):
        with patch.dict(os.environ, {"INTERNAL_API_KEY": INTERNAL_SECRET}), \
                patch.object(internal.file_repository, "upload_file", return_value=True):
            response = await internal_client.post(
                f"/evaluations/{self.evaluation.pid}/artifacts",
                data={"evaluation_plugin_uuid": str(self.run.pid), **form},
                FILES={"file": SimpleUploadedFile(name, b"{}")},
                headers={"X-Internal-Secret": INTERNAL_SECRET})
        self.assertEqual(200, response.status_code, response.content[:300])
        return await Artifact.objects.aget(data=response.data["file_name"])

    async def test_an_upload_announced_as_a_conversation_is_marked(self):
        self.assertTrue(_has_mark_field(), "an artifact has no holds_conversation mark")
        stored = await self._upload("answers.jsonl", holds_conversation="true")
        self.assertTrue(stored.holds_conversation)

    async def test_an_upload_named_like_a_conversation_is_marked_even_unannounced(self):
        self.assertTrue(_has_mark_field(), "an artifact has no holds_conversation mark")
        stored = await self._upload("conversations.md")
        self.assertTrue(stored.holds_conversation)


class TheNameRule(TestCase):
    def test_a_name_that_says_conversation_is_marked_even_unannounced(self):
        self.assertIsNotNone(importlib.util.find_spec("aisc_backend.conversation_artifacts"),
                             "the engine has no rule for which artifacts hold a conversation")
        from aisc_backend import conversation_artifacts
        for name in ("conversations.md", "transcript.txt", CALLS, "plugin_execution.log", "chat-history.json"):
            self.assertTrue(conversation_artifacts.named_like_one(name), name)
        for name in ("results.csv", "chart.png"):
            self.assertFalse(conversation_artifacts.named_like_one(name), name)
