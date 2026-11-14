"""The worker's internal secret opens the internal routes only when it is exactly the configured one, and the
comparison takes the same time whatever the guess."""
import os
from unittest import mock

from django.test import SimpleTestCase

from aisc_backend.auth.internal import InternalSharedKeyAuth


class InternalSecretTestCase(SimpleTestCase):
    def test_the_right_secret_opens_and_any_other_or_none_does_not(self):
        auth = InternalSharedKeyAuth()
        with mock.patch.dict(os.environ, {"INTERNAL_API_KEY": "fake-secret"}):
            self.assertEqual(auth.authenticate(None, "fake-secret"), "internal-service")
            for wrong in ("fake-secreT", "", None, "fake-secret "):
                self.assertIsNone(auth.authenticate(None, wrong), repr(wrong))
        with mock.patch.dict(os.environ, {"INTERNAL_API_KEY": ""}):
            self.assertIsNone(auth.authenticate(None, ""))

    def test_the_comparison_is_constant_time(self):
        import hmac

        with mock.patch.dict(os.environ, {"INTERNAL_API_KEY": "fake-secret"}), \
             mock.patch.object(hmac, "compare_digest", return_value=False) as compare:
            opened = InternalSharedKeyAuth().authenticate(None, "fake-secret")
        compare.assert_called_once()
        self.assertIsNone(opened)
