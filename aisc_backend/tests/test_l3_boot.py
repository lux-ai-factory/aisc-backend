"""L3 — Boot test: settings import without the new env vars (dev compat)."""

import importlib
import os
from unittest import mock

from django.test import SimpleTestCase


class BootWithoutNewEnvTests(SimpleTestCase):
    def test_settings_import_without_new_env(self):
        import config.settings as cfg

        env_clean = {
            k: v for k, v in os.environ.items()
            if k not in ("CATALOGUE_INSTALL_ENABLED", "CATALOGUE_TRUSTED_INDEXES")
        }
        with mock.patch.dict(os.environ, env_clean, clear=True):
            try:
                importlib.reload(cfg)
                self.assertIs(cfg.CATALOGUE_INSTALL_ENABLED, False)
                self.assertIsInstance(cfg.CATALOGUE_TRUSTED_INDEXES, list)
            finally:
                importlib.reload(cfg)
