"""Outbound headless service contracts, exercised through httpx transport."""

from unittest import IsolatedAsyncioTestCase, mock

import httpx

from aisc_backend.services import qualification_service as qualification
from aisc_backend.services import report_service as report


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

    async def test_report_contract(self):
        def handler(request):
            import json

            self.assertEqual(request.url.path, "/api/generate")
            self.assertEqual(request.method, "POST")
            self.assertEqual(request.headers["X-AISC-Service-Token"], "secret")
            self.assertEqual(
                json.loads(request.content),
                {"project_pid": "p1", "format": "pdf", "snapshot": {"project": {"name": "P"}}},
            )
            return httpx.Response(
                200, content=b"%PDF-1.7 test", headers={"content-type": "application/pdf"}
            )

        with (
            mock.patch.object(report, "REPORT_SERVICE_URL", "http://report:8000/"),
            mock.patch.object(report, "SERVICE_TOKEN_FOR_APPS", "secret"),
            self.client(report, handler),
        ):
            data, content_type = await report.generate("p1", {"project": {"name": "P"}})
            self.assertTrue(data.startswith(b"%PDF-"))
            self.assertEqual(content_type, "application/pdf")

    async def test_configured_failures_are_not_successful_fallbacks(self):
        for module, setting, error, call in [
            (
                qualification,
                "QUALIFICATION_SERVICE_URL",
                qualification.QualificationServiceError,
                lambda: qualification.build_card("p", "q", {}, {}),
            ),
            (
                report,
                "REPORT_SERVICE_URL",
                report.ReportServiceError,
                lambda: report.generate("p", {}),
            ),
        ]:
            for status in (401, 503):
                with (
                    mock.patch.object(module, setting, "http://service"),
                    self.client(module, lambda r: httpx.Response(status)),
                ):
                    with self.assertRaises(error):
                        await call()

            def offline(request):
                raise httpx.ConnectError("offline")

            with mock.patch.object(module, setting, "http://service"), self.client(module, offline):
                with self.assertRaises(error):
                    await call()

    async def test_invalid_documents_and_json(self):
        with (
            mock.patch.object(report, "REPORT_SERVICE_URL", "http://report"),
            self.client(
                report,
                lambda r: httpx.Response(
                    200, text="<html>error</html>", headers={"content-type": "text/html"}
                ),
            ),
        ):
            with self.assertRaises(report.ReportServiceError):
                await report.generate("p", {})
        with (
            mock.patch.object(qualification, "QUALIFICATION_SERVICE_URL", "http://qualification"),
            self.client(qualification, lambda r: httpx.Response(200, text="not JSON")),
        ):
            with self.assertRaises(qualification.QualificationServiceError):
                await qualification.get_default_questionnaire()
