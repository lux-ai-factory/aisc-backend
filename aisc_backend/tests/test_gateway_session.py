"""The gateway is the only session.

Every request reaches this service through oauth2-proxy, which holds a session
it refreshes and knows exactly who is behind it. The page in front of this API
used to keep a second session of its own, which nobody refreshed and which the
realm dropped after thirty idle minutes: the page then said "please sign in"
and every call here answered 401 while the gateway's own cookie was still
perfectly valid.

So the token may arrive two ways, and both are the same session: in the
Authorization header, as a page that has one still sends it, or in the header
the gateway sets when it has vouched for the request. What must not change is
that a request carrying neither is refused.
"""

from unittest.mock import patch

from django.test import TestCase
from ninja.testing import TestClient

from aisc_backend.auth.keycloak import KeycloakAuth

CLAIMS = {"preferred_username": "user", "realm_access": {"roles": ["primary-user"]}}


def _router():
    from ninja import Router

    router = Router(auth=KeycloakAuth())

    @router.get("/who")
    def who(request):
        claims = request.auth
        return {"username": claims.get("preferred_username") if isinstance(claims, dict) else None}

    return router


class GatewaySessionTestCase(TestCase):
    """AUTH_ENABLED is on for these: with it off everything passes and the
    question this asks cannot be answered."""

    def setUp(self):
        self.enabled = patch("aisc_backend.auth.keycloak.AUTH_ENABLED", True)
        self.enabled.start()
        self.addCleanup(self.enabled.stop)
        self.client = TestClient(_router())

    def test_a_page_that_still_holds_a_token_is_let_through(self):
        with patch("aisc_backend.auth.keycloak.verify_token", return_value=CLAIMS):
            response = self.client.get("/who", headers={"Authorization": "Bearer a.token"})
        self.assertEqual(200, response.status_code)
        self.assertEqual("user", response.json()["username"])

    def test_the_gateway_vouching_is_enough_on_its_own(self):
        """No Authorization header at all: the page holds no token, and the
        gateway has put the access token it already has on the request."""
        with patch("aisc_backend.auth.keycloak.verify_token", return_value=CLAIMS) as verify:
            response = self.client.get(
                "/who", headers={"X-Auth-Request-Access-Token": "gateway.token"}
            )
        self.assertEqual(200, response.status_code)
        self.assertEqual("user", response.json()["username"])
        verify.assert_called_once_with("gateway.token")

    def test_a_request_with_neither_is_refused(self):
        response = self.client.get("/who")
        self.assertEqual(401, response.status_code)

    def test_a_token_the_realm_did_not_sign_is_refused_whichever_header_it_came_in(self):
        import jwt

        for headers in (
            {"Authorization": "Bearer forged"},
            {"X-Auth-Request-Access-Token": "forged"},
        ):
            with patch(
                "aisc_backend.auth.keycloak.verify_token",
                side_effect=jwt.InvalidSignatureError("nope"),
            ):
                response = self.client.get("/who", headers=headers)
            self.assertEqual(401, response.status_code, headers)
