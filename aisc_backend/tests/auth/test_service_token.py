"""
Tests for the service-token door (aisc_backend.auth.service_token).

UNIT — no live service, no database (SimpleTestCase). The door refuses when no
secret is configured (default: closed) and accepts exactly the configured
token; and the outbound helper only adds the header when a token is set.
"""
import unittest.mock as mock

from django.test import SimpleTestCase
from ninja.testing import TestClient

from aisc_backend.auth import service_token as st


class ServiceTokenAuthTest(SimpleTestCase):

    def setUp(self):
        # Default: door closed unless a test opens it.
        self.p = mock.patch.object(st, "SERVICE_TOKEN", "")
        self.p.start()
        self.addCleanup(self.p.stop)

    def _make_router_client(self):
        from ninja import Router

        r = Router(auth=st.ServiceTokenAuth())

        @r.get("/ping")
        def ping(request):
            return {"ok": True}

        return TestClient(r)

    def test_door_closed_without_configured_token(self):
        client = self._make_router_client()
        resp = client.get("/ping", headers={"X-AISC-Service-Token": "whatever"})
        self.assertEqual(resp.status_code, 401)

    def test_wrong_token_refused(self):
        with mock.patch.object(st, "SERVICE_TOKEN", "s3cret"):
            client = self._make_router_client()
            resp = client.get("/ping", headers={"X-AISC-Service-Token": "wrong"})
        self.assertEqual(resp.status_code, 401)

    def test_correct_token_accepted(self):
        with mock.patch.object(st, "SERVICE_TOKEN", "s3cret"):
            client = self._make_router_client()
            resp = client.get("/ping", headers={"X-AISC-Service-Token": "s3cret"})
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()["ok"])

    def test_missing_header_refused(self):
        with mock.patch.object(st, "SERVICE_TOKEN", "s3cret"):
            client = self._make_router_client()
            resp = client.get("/ping")
        self.assertEqual(resp.status_code, 401)


class ServiceTokenHeadersTest(SimpleTestCase):

    def test_no_header_when_unconfigured(self):
        with mock.patch.object(st, "SERVICE_TOKEN_FOR_APPS", ""):
            self.assertEqual(st.service_token_headers(), {})

    def test_header_when_configured(self):
        with mock.patch.object(st, "SERVICE_TOKEN_FOR_APPS", "outbound-token"):
            self.assertEqual(
                st.service_token_headers(),
                {"X-AISC-Service-Token": "outbound-token"},
            )
