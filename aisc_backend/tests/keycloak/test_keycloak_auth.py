"""
UNIT tests for aisc_backend.auth.keycloak — NO live Keycloak, NO database (SimpleTestCase).

Strategy: generate an RSA keypair in-test, sign test tokens with the PRIVATE key, and patch the
auth module so its JWKS lookup returns our matching PUBLIC key. Then assert verify_token() and the
role gating behave for valid / expired / tampered / wrong-issuer tokens.

Run:  uv run manage.py test aisc_backend.tests.test_keycloak_auth
"""
import datetime
import unittest.mock as mock

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from django.test import SimpleTestCase

from aisc_backend.auth import keycloak

ISSUER = "http://keycloak:8080/realms/aisc"


class _FakeKey:
    """Mimics PyJWKClient's signing key object (only needs a `.key` attribute)."""
    def __init__(self, key):
        self.key = key


class KeycloakVerifyTokenTest(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        cls.public_key = cls.private_key.public_key()

    def setUp(self):
        # Configure the module via patches that AUTO-RESTORE after each test, so we don't leak
        # KEYCLOAK_ISSUER / AUTH_ENABLED into other test modules (e.g. the integration tests).
        fake_client = mock.Mock(
            get_signing_key_from_jwt=mock.Mock(return_value=_FakeKey(self.public_key))
        )
        for patcher in (
            mock.patch.object(keycloak, "AUTH_ENABLED", True),
            mock.patch.object(keycloak, "KEYCLOAK_ISSUER", ISSUER),
            mock.patch.object(keycloak, "_get_jwks_client", return_value=fake_client),
        ):
            self.addCleanup(patcher.stop)
            patcher.start()

    def _make_token(self, *, roles=None, issuer=ISSUER, exp_delta_seconds=300, key=None, azp="aisc-gateway"):
        now = datetime.datetime.now(datetime.timezone.utc)
        payload = {
            "preferred_username": "tester",
            "iss": issuer,
            "azp": azp,
            "iat": now,
            "exp": now + datetime.timedelta(seconds=exp_delta_seconds),
            "realm_access": {"roles": roles or []},
        }
        return jwt.encode(payload, key or self.private_key, algorithm="RS256")

    # --- authentication (verify_token) ---
    def test_valid_token_returns_claims(self):
        token = self._make_token(roles=["admin"])
        claims = keycloak.verify_token(token)
        self.assertEqual(claims["preferred_username"], "tester")
        self.assertIn("admin", keycloak.get_roles(claims))

    def test_expired_token_rejected(self):
        token = self._make_token(exp_delta_seconds=-10)
        with self.assertRaises(jwt.PyJWTError):
            keycloak.verify_token(token)

    def test_tampered_token_rejected(self):
        token = self._make_token(roles=["admin"])[:-3] + "aaa"
        with self.assertRaises(jwt.PyJWTError):
            keycloak.verify_token(token)

    def test_wrong_issuer_rejected(self):
        token = self._make_token(issuer="http://evil.example/realms/x")
        with self.assertRaises(jwt.PyJWTError):
            keycloak.verify_token(token)

    # --- which client the token was issued to (azp) ---
    def test_the_gateway_and_the_engine_s_own_app_are_accepted(self):
        for client in ("aisc-gateway", "aisc-webapp"):
            with self.subTest(client=client):
                self.assertEqual(keycloak.verify_token(self._make_token(azp=client))["azp"], client)

    def test_a_token_issued_to_another_client_of_the_realm_is_rejected(self):
        for client in ("aisc-report-composer", "", None):
            with self.subTest(client=client), self.assertRaises(jwt.PyJWTError):
                keycloak.verify_token(self._make_token(azp=client))

    def test_a_token_issued_to_another_client_is_401(self):
        self.assertIsNone(keycloak.KeycloakAuth().authenticate(mock.Mock(), self._make_token(azp="other")))

    def test_the_accepted_clients_are_a_setting(self):
        with mock.patch.object(keycloak, "ACCEPTED_CLIENTS", ("other",)):
            self.assertEqual(keycloak.verify_token(self._make_token(azp="other"))["azp"], "other")
            with self.assertRaises(jwt.PyJWTError):
                keycloak.verify_token(self._make_token(azp="aisc-gateway"))

    # --- authorization (require_role) ---
    def test_require_role_admin_allows_admin(self):
        token = self._make_token(roles=["admin"])
        result = keycloak.require_role("admin").authenticate(mock.Mock(), token)
        self.assertTrue(result)
        self.assertEqual(result["preferred_username"], "tester")

    def test_require_role_admin_blocks_primary_user(self):
        token = self._make_token(roles=["primary-user"])
        result = keycloak.require_role("admin").authenticate(mock.Mock(), token)
        self.assertIsNone(result)


class AcceptedClientsSetting(SimpleTestCase):
    def test_the_default_is_the_gateway_and_the_engine_s_own_app(self):
        from django.conf import settings

        self.assertEqual(list(settings.ENGINE_ACCEPTED_CLIENTS), ["aisc-gateway", "aisc-webapp"])
        self.assertEqual(list(keycloak.ACCEPTED_CLIENTS), ["aisc-gateway", "aisc-webapp"])

    def test_it_is_read_comma_separated(self):
        from aisc_backend.tests.isolation_support import settings_probe

        result = settings_probe("from django.conf import settings\nout = list(settings.ENGINE_ACCEPTED_CLIENTS)",
                                {"ENGINE_ACCEPTED_CLIENTS": "aisc-gateway, other-client"})
        self.assertEqual(result, {"ok": True, "out": ["aisc-gateway", "other-client"]})
