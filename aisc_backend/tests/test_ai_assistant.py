"""Which model an assistant talks to, and which endpoints it may not (pilot parity).

The assistants (risk mapper, card agent) all resolve their model through
`services.ai_assistant.resolve_chat`: the project's own helper model first, the
stack setting second, and an out-and-out refusal when neither is set up. A helper
is the project's own endpoint, so it goes through the same allowed-hosts door as a
connection test (CON-01) — read before the call, never after.
"""
from django.test import TestCase, override_settings

from aisc_backend.models.connection import Helper
from aisc_backend.models.project import Project
from aisc_backend.models.project_member import MembershipRole, ProjectMember
from aisc_backend.services import ai_assistant, connection_secrets


async def _project(name="assistant", role=MembershipRole.Owner):
    project = await Project.objects.acreate(name=name, description="", status="Ready")
    if role is not None:
        await ProjectMember.objects.acreate(
            project=project, subject="development", role=role,
        )
    return project


@override_settings(CONTROL_OBJECTIVES_LLM_BASE_URL="", CARD_AGENT_LLM_BASE_URL="")
class ResolveChatTest(TestCase):

    async def test_no_model_anywhere_says_what_to_configure(self):
        project = await _project()
        with self.assertRaises(ai_assistant.NoModelConfigured) as caught:
            await ai_assistant.resolve_chat(
                project,
                purpose="the risk mapper",
                base_url_setting="CONTROL_OBJECTIVES_LLM_BASE_URL",
                model_setting="CONTROL_OBJECTIVES_LLM_MODEL",
                api_key_setting="CONTROL_OBJECTIVES_LLM_API_KEY",
            )
        self.assertIn("CONTROL_OBJECTIVES_LLM_BASE_URL", str(caught.exception))

    @override_settings(CONTROL_OBJECTIVES_LLM_BASE_URL="https://models.example/v1")
    async def test_the_stack_setting_is_the_fallback(self):
        project = await _project()
        chat = await ai_assistant.resolve_chat(
            project,
            purpose="the risk mapper",
            base_url_setting="CONTROL_OBJECTIVES_LLM_BASE_URL",
            model_setting="CONTROL_OBJECTIVES_LLM_MODEL",
            api_key_setting="CONTROL_OBJECTIVES_LLM_API_KEY",
        )
        self.assertTrue(callable(chat))

    @override_settings(CONTROL_OBJECTIVES_LLM_BASE_URL="")
    async def test_a_public_helper_model_wins_over_the_stack_setting(self):
        project = await _project()
        await Helper.objects.acreate(
            project=project, name="judge", label="gpt-4o-mini",
            endpoint_url="https://models.example/v1", roles=["judge"],
        )
        await connection_secrets.set_secret(
            project.pk, connection_secrets.helper_key_name("judge"), "sk-test"
        )
        chat = await ai_assistant.resolve_chat(
            project,
            purpose="the risk mapper",
            base_url_setting="CONTROL_OBJECTIVES_LLM_BASE_URL",
            model_setting="CONTROL_OBJECTIVES_LLM_MODEL",
            api_key_setting="CONTROL_OBJECTIVES_LLM_API_KEY",
        )
        self.assertTrue(callable(chat))

    async def test_a_helper_at_an_internal_address_is_refused_before_the_call(self):
        project = await _project()
        await Helper.objects.acreate(
            project=project, name="judge", label="",
            endpoint_url="http://169.254.169.254/latest", roles=["judge"],
        )
        with self.assertRaises(ai_assistant.DestinationRefused) as caught:
            await ai_assistant.resolve_chat(
                project,
                purpose="the risk mapper",
                base_url_setting="CONTROL_OBJECTIVES_LLM_BASE_URL",
                model_setting="CONTROL_OBJECTIVES_LLM_MODEL",
                api_key_setting="CONTROL_OBJECTIVES_LLM_API_KEY",
            )
        self.assertIn("judge", str(caught.exception))

    async def test_a_helper_on_the_private_network_is_refused_unless_listed(self):
        project = await _project()
        await Helper.objects.acreate(
            project=project, name="judge", label="",
            endpoint_url="http://10.0.0.5:11434/v1", roles=["judge"],
        )
        with self.assertRaises(ai_assistant.DestinationRefused):
            await ai_assistant.resolve_chat(
                project,
                purpose="the risk mapper",
                base_url_setting="CONTROL_OBJECTIVES_LLM_BASE_URL",
                model_setting="CONTROL_OBJECTIVES_LLM_MODEL",
                api_key_setting="CONTROL_OBJECTIVES_LLM_API_KEY",
            )

        # listing it in the project's allowed hosts opens the door for the next call
        from aisc_backend.models.connection import AllowedHost

        await AllowedHost.objects.acreate(project=project, host="10.0.0.5:11434")
        chat = await ai_assistant.resolve_chat(
            project,
            purpose="the risk mapper",
            base_url_setting="CONTROL_OBJECTIVES_LLM_BASE_URL",
            model_setting="CONTROL_OBJECTIVES_LLM_MODEL",
            api_key_setting="CONTROL_OBJECTIVES_LLM_API_KEY",
        )
        self.assertTrue(callable(chat))

    async def test_a_helper_is_asked_with_its_stored_key(self):
        project = await _project()
        await Helper.objects.acreate(
            project=project, name="judge", label="my-model",
            endpoint_url="https://models.example/v1", roles=["judge"],
        )
        await connection_secrets.set_secret(
            project.pk, connection_secrets.helper_key_name("judge"), "sk-secret"
        )
        captured: dict = {}

        class FakeClient:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

            async def post(self, url, json=None, headers=None):
                captured["url"] = url
                captured["json"] = json
                captured["headers"] = headers

                class Response:
                    status_code = 200

                    def json(self):
                        return {"choices": [{"message": {"content": "hello"}}]}

                return Response()

        import httpx

        original = httpx.AsyncClient
        httpx.AsyncClient = lambda **kwargs: FakeClient()  # type: ignore[assignment]
        try:
            chat = await ai_assistant.resolve_chat(
                project,
                purpose="the risk mapper",
                base_url_setting="CONTROL_OBJECTIVES_LLM_BASE_URL",
                model_setting="CONTROL_OBJECTIVES_LLM_MODEL",
                api_key_setting="CONTROL_OBJECTIVES_LLM_API_KEY",
            )
            answer = await chat([{"role": "user", "content": "hi"}])
        finally:
            httpx.AsyncClient = original  # type: ignore[assignment]

        self.assertEqual(answer, "hello")
        self.assertEqual(captured["url"], "https://models.example/v1/chat/completions")
        self.assertEqual(captured["json"]["model"], "my-model")
        self.assertEqual(captured["headers"]["Authorization"], "Bearer sk-secret")
