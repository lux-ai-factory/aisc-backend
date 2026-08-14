"""L3 — Level 3 settings integration (defaults + override)."""

import importlib
import os
from unittest import mock

from django.test import SimpleTestCase, override_settings


class SettingsIntegrationTests(SimpleTestCase):
    def test_flag_off_by_default(self):
        from django.conf import settings

        self.assertIs(settings.CATALOGUE_INSTALL_ENABLED, False)

    @override_settings(
        CATALOGUE_TRUSTED_INDEXES=["https://override.example.com/x/+simple/"]
    )
    def test_allowlist_override_env(self):
        from django.conf import settings

        self.assertEqual(
            settings.CATALOGUE_TRUSTED_INDEXES,
            ["https://override.example.com/x/+simple/"],
        )

    def test_allowlist_derived_from_registry(self):
        # With PACKAGE_REGISTRY_URL + INDEX set and no explicit allowlist,
        # the default derives the "+simple" index URL.
        import config.settings as cfg

        env = {
            "PACKAGE_REGISTRY_URL": "https://registry.example.com",
            "PACKAGE_REGISTRY_INDEX": "aisc",
        }
        # Do not let a stray explicit allowlist leak into the reload.
        env_clean = {k: v for k, v in os.environ.items()
                     if k != "CATALOGUE_TRUSTED_INDEXES"}
        with mock.patch.dict(os.environ, {**env_clean, **env}, clear=True):
            try:
                importlib.reload(cfg)
                self.assertIn(
                    "https://registry.example.com/aisc/+simple/",
                    cfg.CATALOGUE_TRUSTED_INDEXES,
                )
            finally:
                # Restore the module to the real process environment.
                importlib.reload(cfg)
