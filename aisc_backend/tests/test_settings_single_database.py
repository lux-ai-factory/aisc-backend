"""The settings keep nothing of the engine's old single-database layout: every project has its own database."""
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


class NoSingleDatabaseCode(unittest.TestCase):
    def test_settings_keep_no_dead_single_database_code(self):
        text = (ROOT / "config" / "settings.py").read_text()
        self.assertNotIn("_single_database", text)
        self.assertNotIn("DB_SCHEMA", text)
