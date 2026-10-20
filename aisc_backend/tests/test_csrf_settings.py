"""No CSRF trusted origins: the engine's API checks bearer tokens, not cookies, so no origin needs trusting."""
from django.conf import settings
from django.test import SimpleTestCase


class NoTrustedOrigins(SimpleTestCase):
    def test_the_misspelt_setting_is_gone(self):
        self.assertFalse(hasattr(settings, "CRSF_TRUSTED_ORIGINS"))

    def test_django_keeps_its_default(self):
        self.assertFalse(settings.is_overridden("CSRF_TRUSTED_ORIGINS"))
        self.assertEqual(settings.CSRF_TRUSTED_ORIGINS, [])
