"""The engine reads LEDGER_MODE strictly and records only inside the Configurator.

A misspelt mode is refused at start in either deployment mode, instead of silently meaning off. Inside the
Configurator an absent mode means record; a standalone engine records nothing whatever the mode says.
"""
import os
from unittest.mock import patch

from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase, override_settings

from aisc_backend import deployment, ledger

CONFIGURATOR_OK = {"DB_ENGINE": "django.db.backends.postgresql", "DJANGO_SECRET_KEY": "s", "RUN_TICKET_KEY": "t",
                   "AUTH_ENABLED": "true"}


class TheLedgerModeIsReadStrictly(SimpleTestCase):

    def test_a_misspelt_ledger_mode_is_refused_in_either_mode(self):
        for mode in ("standalone", "configurator"):
            with self.subTest(mode=mode), self.assertRaisesRegex(ImproperlyConfigured, "LEDGER_MODE"):
                deployment.check_environment({**CONFIGURATOR_OK, "AISC_DEPLOYMENT": mode, "LEDGER_MODE": "recrod"},
                                             testing=True)

    def test_an_empty_ledger_mode_is_refused(self):
        with self.assertRaisesRegex(ImproperlyConfigured, "LEDGER_MODE"):
            deployment.check_environment({**CONFIGURATOR_OK, "AISC_DEPLOYMENT": "configurator", "LEDGER_MODE": ""},
                                         testing=True)

    def test_the_settings_source_carries_the_ledger_mode(self):
        self.assertEqual(deployment.settings_source({"LEDGER_MODE": "record"}.get), {"LEDGER_MODE": "record"})

    def test_standalone_records_nothing_whatever_ledger_mode_says(self):
        with override_settings(AISC_DEPLOYMENT="standalone"), patch.dict(os.environ, {"LEDGER_MODE": "record"}):
            self.assertFalse(ledger.on())

    def test_configurator_records_when_the_mode_is_absent(self):
        with override_settings(AISC_DEPLOYMENT="configurator"), patch.dict(os.environ, {}):
            os.environ.pop("LEDGER_MODE", None)
            self.assertTrue(ledger.on())
