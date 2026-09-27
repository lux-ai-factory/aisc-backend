"""Sean's engine code of 2026-09-23 stays byte-identical to e34fca3.

RULES.md freezes it, and amendment A1 of pipeline-2026-09-23/03-specs.md says the
test stamp (WP9) changes no line in it: S9.1 is read as "routers/evaluation.py
and models/evaluation.py equal e34fca3". WP1's revert brings the files 0022's
work changed back to e34fca3, which also restores Sean's three tests (S1.5).

The list is every file seanblevins committed on 2026-09-23 that still exists at
e34fca3 (the squashed migrations do not). Skips when git or the commit is not
available (a container image without .git).
"""
import pathlib
import shutil
import subprocess
import unittest

REPO = pathlib.Path(__file__).resolve().parents[2]
REFERENCE = "e34fca3"

SEAN_FILES_2026_09_23 = [
    "aisc_backend/models/ai_system.py",
    "aisc_backend/models/common.py",
    "aisc_backend/models/evaluation.py",
    "aisc_backend/models/plugin.py",
    "aisc_backend/models/project_config.py",
    "aisc_backend/models/project.py",
    "aisc_backend/repositories/evaluation_repository.py",
    "aisc_backend/repositories/project_config_repository.py",
    "aisc_backend/repositories/project_repository.py",
    "aisc_backend/repositories/stats_repository.py",
    "aisc_backend/routers/component.py",
    "aisc_backend/routers/evaluation.py",
    "aisc_backend/routers/internal.py",
    "aisc_backend/routers/plugin.py",
    "aisc_backend/routers/project_config.py",
    "aisc_backend/routers/project.py",
    "aisc_backend/schemas/ai_system.py",
    "aisc_backend/schemas/plugin.py",
    "aisc_backend/schemas/project_config.py",
    "aisc_backend/services/datashape_validation.py",
    "aisc_backend/services/project_config_keys.py",
    "aisc_backend/services/project_config_matching.py",
    "aisc_backend/tests/routers/test_evaluation_inputs_template.py",
    "aisc_backend/tests/routers/test_project_config_router.py",
    "aisc_backend/tests/routers/test_project_evaluations_serialization.py",
    "config/settings.py",
    "env.development",
    "pyproject.toml",
]


def _reference(path: str) -> bytes | None:
    try:
        return subprocess.run(["git", "-C", str(REPO), "show", f"{REFERENCE}:{path}"],
                              check=True, capture_output=True).stdout
    except subprocess.CalledProcessError:
        return None


@unittest.skipUnless(shutil.which("git") and (REPO / ".git").exists(), "needs the git checkout")
class SeanFilesAreE34fca3(unittest.TestCase):

    def _assert_identical(self, path):
        expected = _reference(path)
        if expected is None:
            self.skipTest(f"{REFERENCE} is not in this clone")
        actual = (REPO / path).read_bytes()
        self.assertEqual(actual, expected, f"{path} differs from {REFERENCE}")

    def test_s9_1_evaluation_router_is_e34fca3(self):
        self._assert_identical("aisc_backend/routers/evaluation.py")

    def test_s9_1_evaluation_model_is_e34fca3(self):
        self._assert_identical("aisc_backend/models/evaluation.py")

    def test_s1_5_sean_tests_are_e34fca3(self):
        for path in SEAN_FILES_2026_09_23:
            if "/tests/" in path:
                with self.subTest(path=path):
                    self._assert_identical(path)

    def test_g4_every_sean_file_is_e34fca3(self):
        for path in SEAN_FILES_2026_09_23:
            with self.subTest(path=path):
                self._assert_identical(path)
