"""L4 — Boot test: settings import without CATALOGUE_INSTALL_TOKEN (dev compat).

The install token must never be mandatory at boot: importing ``config.settings``
with no token set must succeed and default to an empty string, so an existing dev
Platform boots unchanged (E7/E8).
"""

import importlib
import os
from unittest import mock

from django.test import SimpleTestCase


class BootWithoutInstallTokenTests(SimpleTestCase):
    def test_settings_import_without_install_token(self):
        import config.settings as cfg

        env_clean = {
            k: v for k, v in os.environ.items() if k != "CATALOGUE_INSTALL_TOKEN"
        }
        with mock.patch.dict(os.environ, env_clean, clear=True):
            try:
                importlib.reload(cfg)
                self.assertEqual(cfg.CATALOGUE_INSTALL_TOKEN, "")
            finally:
                importlib.reload(cfg)
