"""Sean's files the configurator no longer edits (adapt plan 2026-09-28, items 2, 3, 7)."""
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
UNCHANGED = [
    "aisc_backend/routers/component.py", "aisc_backend/routers/evaluation.py",
    "aisc_backend/routers/file.py", "aisc_backend/routers/project_config.py",
    "aisc_backend/routers/stats.py", "aisc_backend/routers/task.py",
    "aisc_backend/routers/project.py", "aisc_backend/auth/keycloak.py",
]


def _master(path: str) -> str | None:
    r = subprocess.run(["git", "show", f"origin/master:{path}"], cwd=ROOT, capture_output=True, text=True)
    return r.stdout if r.returncode == 0 else None


class SeansFilesUnchanged(unittest.TestCase):
    def test_each_file_equals_master(self):
        if _master("manage.py") is None:
            self.skipTest("no git history here (a built image)")
        for path in UNCHANGED:
            self.assertEqual((ROOT / path).read_text(), _master(path), path)

    def test_settings_keep_no_dead_single_database_code(self):
        text = (ROOT / "config" / "settings.py").read_text()
        self.assertNotIn("_single_database", text)
        self.assertNotIn("DB_SCHEMA", text)
