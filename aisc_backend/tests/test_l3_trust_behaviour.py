"""L3 — Level 2 behaviour tests (E6): right source + exact version."""

from django.test import SimpleTestCase

from aisc_backend.services.catalogue_install import is_trusted_index, pin_requirement

ALLOWLIST = ["https://registry.example.com/aisc/+simple/"]


class TrustBehaviourTests(SimpleTestCase):
    def test_untrusted_index_is_refused(self):
        # Given an allowlist, when we check an index outside it, then it is refused
        # — the basis for L4 refusing to install from an untrusted source.
        self.assertFalse(
            is_trusted_index("https://pypi.org/simple/", ALLOWLIST)
        )

    def test_trusted_index_pins_exact_version(self):
        # Given a trusted index and an exact version, when we prepare the target,
        # then we obtain pkg==version (never a range).
        self.assertTrue(
            is_trusted_index("https://registry.example.com/aisc/+simple/", ALLOWLIST)
        )
        self.assertEqual(pin_requirement("agentdojo", "0.1.35"), "agentdojo==0.1.35")
        with self.assertRaises(ValueError):
            pin_requirement("agentdojo", ">=0.1.0")
