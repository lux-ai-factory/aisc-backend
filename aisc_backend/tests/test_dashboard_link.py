"""The dashboard link is authenticated and scoped (ANA-01).

A link carries its scope (project, AI-card version, target, tool) and an expiry,
and is signed with the key the dashboard also holds — so it cannot be minted or
edited without the key, and it verifies for exactly one project. With no key
configured nothing is minted: `build_link` returns None rather than an
unauthenticated URL into a dashboard that holds every project's data.
"""
from django.test import TestCase, override_settings

from aisc_backend.services import dashboard_link as dl

BASE = "https://superset.example/superset/dashboard/ai-system"
KEY = "the-dashboard-link-key"


@override_settings(RESULTS_DASHBOARD_SECRET=KEY, RESULTS_DASHBOARD_LINK_TTL=900)
class SignAndVerifyTest(TestCase):

    def _scope(self, **over):
        scope = {"project": "11111111-1111-1111-1111-111111111111",
                 "version": "3", "target": None, "tool": "fairness"}
        scope.update(over)
        return scope

    def test_a_link_round_trips_with_its_scope_and_expiry(self):
        url = dl.build_link(BASE, self._scope(), now=1000.0)
        self.assertIn(BASE + "?", url)
        scope, refusal = dl.verify_link(url, project=self._scope()["project"], now=1001.0)
        self.assertIsNone(refusal)
        self.assertEqual(scope["project"], self._scope()["project"])
        self.assertEqual(scope["version"], "3")
        self.assertEqual(scope["tool"], "fairness")
        self.assertIsNone(scope["target"])
        self.assertEqual(scope[dl.EXPIRY_PARAM], 1900)

    def test_no_key_means_no_link_at_all(self):
        with override_settings(RESULTS_DASHBOARD_SECRET=""):
            self.assertIsNone(dl.build_link(BASE, self._scope()))
            self.assertIsNone(dl.sign_scope(self._scope()))

    def test_no_base_means_no_link(self):
        self.assertIsNone(dl.build_link("", self._scope()))

    def test_a_link_without_a_signature_is_refused(self):
        scope, refusal = dl.verify_link(f"{BASE}?project=x&version=1", key=KEY)
        self.assertIsNone(scope)
        self.assertEqual(refusal, dl.NO_SIGNATURE)

    def test_an_edited_scope_is_refused(self):
        """Changing the project (or version, or tool) breaks the signature."""
        url = dl.build_link(BASE, self._scope(), now=1000.0)
        for edited in (
            url.replace("tool=fairness", "tool=robustness"),
            url.replace("version=3", "version=4"),
            url.replace(self._scope()["project"], "22222222-2222-2222-2222-222222222222"),
        ):
            scope, refusal = dl.verify_link(edited, key=KEY, now=1001.0)
            self.assertIsNone(scope)
            self.assertEqual(refusal, dl.BAD_SIGNATURE)

    def test_an_edited_expiry_is_refused(self):
        url = dl.build_link(BASE, self._scope(), now=1000.0)
        scope, refusal = dl.verify_link(url.replace("exp=1900", "exp=9999999999"), key=KEY,
                                        now=1001.0)
        self.assertIsNone(scope)
        self.assertEqual(refusal, dl.BAD_SIGNATURE)

    def test_an_expired_link_is_refused(self):
        url = dl.build_link(BASE, self._scope(), now=1000.0, ttl=60)
        scope, refusal = dl.verify_link(url, project=self._scope()["project"], now=1061.0)
        self.assertIsNone(scope)
        self.assertEqual(refusal, dl.EXPIRED)

    def test_a_link_for_another_project_is_told_apart_from_a_forgery(self):
        url = dl.build_link(BASE, self._scope(), now=1000.0)
        scope, refusal = dl.verify_link(
            url, project="22222222-2222-2222-2222-222222222222", key=KEY, now=1001.0
        )
        self.assertIsNone(scope)
        self.assertEqual(refusal, dl.WRONG_PROJECT)

    def test_another_key_cannot_verify_or_forge_it(self):
        url = dl.build_link(BASE, self._scope(), now=1000.0)
        _, refusal = dl.verify_link(url, key="another-key", now=1001.0)
        self.assertEqual(refusal, dl.BAD_SIGNATURE)

    def test_with_no_key_configured_verification_says_so(self):
        with override_settings(RESULTS_DASHBOARD_SECRET=""):
            _, refusal = dl.verify_link(f"{BASE}?project=x", key="")
        self.assertEqual(refusal, dl.NO_SECRET)
