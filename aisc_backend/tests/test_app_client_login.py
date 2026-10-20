"""The standalone engine's app-client login (allauth headless) hands back a JWT pair next to the session token."""
import unittest

from allauth.account.models import EmailAddress
from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import TestCase
from ninja_jwt.tokens import AccessToken, RefreshToken

from aisc_backend import deployment


@unittest.skipIf(settings.AISC_DEPLOYMENT == deployment.CONFIGURATOR, "standalone only: the Configurator has no login")
class AppClientLogin(TestCase):
    def test_login_returns_a_jwt_pair(self):
        user = get_user_model().objects.create_user(username="u", email="u@example.com", password="pw-123456!")
        EmailAddress.objects.create(user=user, email=user.email, verified=True, primary=True)

        response = self.client.post("/_allauth/app/v1/auth/login",
                                    {"email": "u@example.com", "password": "pw-123456!"},
                                    content_type="application/json")

        self.assertEqual(response.status_code, 200, response.content)
        meta = response.json()["meta"]
        self.assertEqual(int(AccessToken(meta["access_token"])["user_id"]), user.pk)
        self.assertEqual(int(RefreshToken(meta["refresh_token"])["user_id"]), user.pk)
