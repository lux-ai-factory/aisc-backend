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

Configurator only: standalone has its own login and never reads the gateway header.
Since the adapt plan (2026-09-28, item 2) the door copies the gateway's token into
`Authorization` (`project_door.bearer_from_gateway`) and Sean's bearer check, unchanged,
verifies it; so these go through that copy, then through `KeycloakAuth`.
"""

from unittest.mock import patch

from django.test import RequestFactory, SimpleTestCase

from aisc_backend.auth.keycloak import KeycloakAuth
from aisc_backend.tests.isolation_support import configurator_only
from aisc_backend.tests.test_isolation_engine import DoorCase

CLAIMS = {"preferred_username": "user", "realm_access": {"roles": ["primary-user"]}}


def _through_the_door(headers: dict):
    """What Sean's bearer check answers for a request after the door's copy."""
    from aisc_backend import project_door

    meta = {"HTTP_" + name.upper().replace("-", "_"): value for name, value in headers.items()}
    request = RequestFactory().get("/api/v1/me", **meta)
    request.headers  # noqa: B018 - read once before the copy, as a middleware before the door may
    project_door.bearer_from_gateway(request)
    return KeycloakAuth()(request)


@configurator_only
class GatewaySessionTestCase(SimpleTestCase):
    """AUTH_ENABLED is on for these: with it off everything passes and the
    question this asks cannot be answered."""

    def setUp(self):
        self.enabled = patch("aisc_backend.auth.keycloak.AUTH_ENABLED", True)
        self.enabled.start()
        self.addCleanup(self.enabled.stop)

    def test_a_page_that_still_holds_a_token_is_let_through(self):
        with patch("aisc_backend.auth.keycloak.verify_token", return_value=CLAIMS):
            claims = _through_the_door({"Authorization": "Bearer a.token"})
        self.assertEqual("user", claims["preferred_username"])

    def test_the_gateway_vouching_is_enough_on_its_own(self):
        """No Authorization header at all: the page holds no token, and the
        gateway has put the access token it already has on the request."""
        with patch("aisc_backend.auth.keycloak.verify_token", return_value=CLAIMS) as verify:
            claims = _through_the_door({"X-Auth-Request-Access-Token": "gateway.token"})
        self.assertEqual("user", claims["preferred_username"])
        verify.assert_called_once_with("gateway.token")

    def test_a_request_with_neither_is_refused(self):
        self.assertIsNone(_through_the_door({}))

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
                self.assertIsNone(_through_the_door(headers), headers)


@configurator_only
class TheGatewaySessionThroughTheApi(DoorCase):
    """End to end, through the middleware: a route with no project (`/api/v1/me`) is signed in
    by the gateway's header alone, and a project route is admitted by it."""

    def test_the_gateway_header_alone_signs_in(self):
        token = self.bearer().removeprefix("Bearer ")
        response = self.call("GET", "/api/v1/me", {"X-Auth-Request-Access-Token": token})
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["username"], "alice")

    def test_neither_header_is_401(self):
        self.assertEqual(self.call("GET", "/api/v1/me", {}).status_code, 401)
