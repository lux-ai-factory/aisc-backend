"""L3 — Level 1 unit tests for the pure trust helpers."""

from django.test import SimpleTestCase

from aisc_backend.services.catalogue_install import is_trusted_index, pin_requirement

ALLOWLIST = ["https://registry.example.com/aisc/+simple/"]


class TrustedIndexUnitTests(SimpleTestCase):
    def test_trusted_index_accepts_listed(self):
        self.assertTrue(
            is_trusted_index("https://registry.example.com/aisc/+simple/", ALLOWLIST)
        )

    def test_trusted_index_normalises_trailing_slash(self):
        # ".../+simple" must be treated as ".../+simple/"
        self.assertTrue(
            is_trusted_index("https://registry.example.com/aisc/+simple", ALLOWLIST)
        )

    def test_trusted_index_normalises_host_case(self):
        self.assertTrue(
            is_trusted_index("https://REGISTRY.Example.com/aisc/+simple/", ALLOWLIST)
        )

    def test_trusted_index_rejects_unlisted(self):
        self.assertFalse(
            is_trusted_index("https://evil.example.com/aisc/+simple/", ALLOWLIST)
        )

    def test_trusted_index_rejects_scheme_mismatch(self):
        # same host/path but http vs https => not trusted (no substring match)
        self.assertFalse(
            is_trusted_index("http://registry.example.com/aisc/+simple/", ALLOWLIST)
        )

    def test_trusted_index_rejects_empty_or_bare(self):
        self.assertFalse(is_trusted_index("", ALLOWLIST))
        self.assertFalse(is_trusted_index("registry.example.com/aisc/+simple/", ALLOWLIST))


class PinRequirementUnitTests(SimpleTestCase):
    def test_pin_requirement_exact_version(self):
        self.assertEqual(pin_requirement("agentdojo", "0.1.35"), "agentdojo==0.1.35")

    def test_pin_requirement_accepts_pre_and_local(self):
        self.assertEqual(pin_requirement("pkg", "1.0rc1"), "pkg==1.0rc1")
        self.assertEqual(pin_requirement("pkg", "1.0+cpu"), "pkg==1.0+cpu")

    def test_pin_requirement_rejects_range(self):
        for bad in (">=1.0", "~=1.0", "==1.0", "<2", "1.*", "1,2"):
            with self.assertRaises(ValueError):
                pin_requirement("pkg", bad)

    def test_pin_requirement_rejects_empty(self):
        with self.assertRaises(ValueError):
            pin_requirement("pkg", "")
        with self.assertRaises(ValueError):
            pin_requirement("pkg", None)

    def test_pin_requirement_rejects_dubious(self):
        for bad in (" 1.0", "1.0 ", "1.0; rm -rf", "1.0 && x"):
            with self.assertRaises(ValueError):
                pin_requirement("pkg", bad)

    def test_pin_requirement_rejects_bad_package_name(self):
        with self.assertRaises(ValueError):
            pin_requirement("", "1.0")
        with self.assertRaises(ValueError):
            pin_requirement("bad name", "1.0")

    def test_pin_requirement_rejects_trailing_newline_injection(self):
        # '$' would match before a trailing newline; a package name or version
        # carrying '\n' must be refused so nothing splits into an unpinned line (E6).
        for pkg in ("pkg\n", "pkg\nmalicious", "pkg\r"):
            with self.assertRaises(ValueError):
                pin_requirement(pkg, "1.0")
        for ver in ("1.0\n", "1.0\nmalicious", "1.0\r"):
            with self.assertRaises(ValueError):
                pin_requirement("pkg", ver)
