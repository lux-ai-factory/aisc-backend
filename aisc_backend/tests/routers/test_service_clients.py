"""Outbound headless service contracts, exercised through httpx transport.

Only the qualification service remains an outbound service (ADR 0006); the report renderer
runs in-process (ADR 0008) and has no transport to exercise here.
"""

from unittest import IsolatedAsyncioTestCase, mock

import httpx

from aisc_backend.services import qualification_service as qualification


class ServiceClientTest(IsolatedAsyncioTestCase):
    def client(self, module, handler):
        real_client = httpx.AsyncClient
        return mock.patch.object(
            module.httpx,
            "AsyncClient",
            side_effect=lambda **kwargs: real_client(
                transport=httpx.MockTransport(handler), **kwargs
            ),
        )

    async def test_qualification_contract(self):
        requests = []

        def handler(request):
            requests.append(request)
            self.assertEqual(request.headers["X-AISC-Service-Token"], "secret")
            return httpx.Response(200, json={"view": {"system": {}}, "digest": "real"})

        with (
            mock.patch.object(
                qualification, "QUALIFICATION_SERVICE_URL", "http://qualification:8000/"
            ),
            mock.patch.object(qualification, "SERVICE_TOKEN_FOR_APPS", "secret"),
            self.client(qualification, handler),
        ):
            self.assertEqual(
                (
                    await qualification.build_card(
                        "p1", "q1", {"name": "Q"}, {"a": 1}, {"name": "S"}
                    )
                )["digest"],
                "real",
            )
            await qualification.get_default_questionnaire()
            await qualification.get_vocabularies()
        self.assertEqual(
            [(r.method, r.url.path) for r in requests],
            [
                ("POST", "/api/build"),
                ("GET", "/api/default-questionnaire"),
                ("GET", "/api/vocabularies"),
            ],
        )
        import json

        self.assertEqual(
            json.loads(requests[0].content),
            {
                "project_pid": "p1",
                "questionnaire_id": "q1",
                "questionnaire": {"name": "Q"},
                "answers": {"a": 1},
                "system": {"name": "S"},
            },
        )

    async def test_configured_failures_are_not_successful_fallbacks(self):
        for status in (401, 503):
            with (
                mock.patch.object(qualification, "QUALIFICATION_SERVICE_URL", "http://service"),
                self.client(qualification, lambda r: httpx.Response(status)),
            ):
                with self.assertRaises(qualification.QualificationServiceError):
                    await qualification.build_card("p", "q", {}, {})

        def offline(request):
            raise httpx.ConnectError("offline")

        with (
            mock.patch.object(qualification, "QUALIFICATION_SERVICE_URL", "http://service"),
            self.client(qualification, offline),
        ):
            with self.assertRaises(qualification.QualificationServiceError):
                await qualification.build_card("p", "q", {}, {})

    async def test_invalid_json(self):
        with (
            mock.patch.object(qualification, "QUALIFICATION_SERVICE_URL", "http://qualification"),
            self.client(qualification, lambda r: httpx.Response(200, text="not JSON")),
        ):
            with self.assertRaises(qualification.QualificationServiceError):
                await qualification.get_default_questionnaire()
