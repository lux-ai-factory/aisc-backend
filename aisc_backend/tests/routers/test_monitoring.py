"""Admin monitoring contract and bounded Flower failure handling."""

import uuid
from unittest import IsolatedAsyncioTestCase, mock

import httpx
from ninja.errors import HttpError
from ninja.testing import TestAsyncClient

from aisc_evaluation.routers.task import router
from aisc_backend.services.monitoring import fetch_tasks

client = TestAsyncClient(router)
TASK_ID = str(uuid.uuid4())


class MonitoringRouterTest(IsolatedAsyncioTestCase):
    async def test_access_roles(self):
        with (
            mock.patch("aisc_backend.auth.keycloak.AUTH_ENABLED", True),
            mock.patch(
                "aisc_backend.auth.keycloak.verify_token",
                side_effect=lambda token: {"realm_access": {"roles": [token]}},
            ),
            mock.patch(
                "aisc_evaluation.routers.task.fetch_tasks",
                new=mock.AsyncMock(return_value={TASK_ID: {"state": "SUCCESS"}}),
            ) as upstream,
        ):
            self.assertEqual((await client.get("/monitoring")).status_code, 401)
            self.assertEqual(
                (
                    await client.get(
                        "/monitoring", headers={"Authorization": "Bearer primary-user"}
                    )
                ).status_code,
                401,
            )
            response = await client.get("/monitoring", headers={"Authorization": "Bearer admin"})
            self.assertEqual(response.status_code, 200)
            self.assertIn(TASK_ID, response.json())
            response = await client.get(
                f"/monitoring/{TASK_ID}", headers={"Authorization": "Bearer admin"}
            )
            self.assertEqual(response.status_code, 200)
            upstream.assert_awaited_with(f"task/info/{TASK_ID}")
            self.assertEqual(
                (
                    await client.get(
                        "/monitoring/not-a-uuid", headers={"Authorization": "Bearer admin"}
                    )
                ).status_code,
                422,
            )


class MonitoringServiceTest(IsolatedAsyncioTestCase):
    async def _request(self, response=None, error=None):
        upstream = mock.AsyncMock()
        upstream.get.side_effect = error
        upstream.get.return_value = response
        with mock.patch("aisc_backend.services.monitoring.httpx.AsyncClient") as factory:
            factory.return_value.__aenter__.return_value = upstream
            result = await fetch_tasks("tasks")
            factory.assert_called_once_with(timeout=10)
            self.assertTrue(upstream.get.call_args.args[0].endswith("/flower/api/tasks"))
            return result

    async def test_success(self):
        response = httpx.Response(
            200, json={TASK_ID: {}}, request=httpx.Request("GET", "http://flower")
        )
        self.assertEqual(await self._request(response), {TASK_ID: {}})

    async def test_failures(self):
        for status, body, expected in [(404, {}, 404), (500, {}, 502), (200, [], 502)]:
            with self.subTest(status=status, body=body):
                response = httpx.Response(
                    status, json=body, request=httpx.Request("GET", "http://flower")
                )
                with self.assertRaises(HttpError) as raised:
                    await self._request(response)
                self.assertEqual(raised.exception.status_code, expected)
        for error, expected in [
            (httpx.ReadTimeout("slow"), 504),
            (httpx.ConnectError("offline"), 502),
        ]:
            with self.assertRaises(HttpError) as raised:
                await self._request(error=error)
            self.assertEqual(raised.exception.status_code, expected)

    async def test_unconfigured_is_a_clean_502(self):
        from django.conf import settings as django_settings

        with mock.patch.object(django_settings, "FLOWER_SERVICE_URL", ""):
            with self.assertRaises(HttpError) as raised:
                await fetch_tasks("tasks")
            self.assertEqual(raised.exception.status_code, 502)
