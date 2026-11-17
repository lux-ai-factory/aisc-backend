"""
The allowed-hosts rules (CON-01), on their own.

A connection may call any public address; an internal one only when the project
lists it or the stack allows it for every project. Three classes can never be
reached: loopback (the platform itself), cloud metadata addresses, and this
deployment's own services — whatever the list says. These tests pin the rules so a
later change to the classify-or-refuse order cannot quietly open a hole.
"""
from django.test import SimpleTestCase, override_settings
from ninja.errors import HttpError

from aisc_backend.services import allowed_hosts as hosts

PUBLIC = "https://api.example.com/v1"


class ClassificationTest(SimpleTestCase):
    """No database: the rules themselves."""

    def test_a_public_address_is_reachable_without_being_listed(self):
        self.assertIsNone(hosts.refusal_for(PUBLIC, allowed=set(), floor=set()))
        self.assertFalse(hosts.is_internal("api.example.com"))

    def test_any_internal_address_is_refused_until_it_is_listed(self):
        for address in (
            "http://10.0.0.5:8500/v1",
            "http://172.16.9.9/ask",
            "http://192.168.1.10:8080/",
            "http://host.docker.internal:8500/",
            "http://box.local/ask",
            "http://assessee:9000/v1",  # a single-label LAN name
            "http://llm.svc.cluster.local/v1",
        ):
            refusal = hosts.refusal_for(address, allowed=set(), floor=set())
            self.assertIsNotNone(refusal, address)
            self.assertEqual(refusal[0], "internal_not_allowed", address)

    def test_an_entry_opens_that_host_and_that_port_only(self):
        address = "http://10.0.0.5:8500/v1"
        self.assertIsNone(hosts.refusal_for(address, allowed={"10.0.0.5:8500"}, floor=set()))
        # the same host on another port is still not that entry
        other = hosts.refusal_for("http://10.0.0.5:8600/v1", allowed={"10.0.0.5:8500"}, floor=set())
        self.assertEqual(other[0], "internal_not_allowed")
        # a bare host entry opens every port of it
        self.assertIsNone(hosts.refusal_for("http://10.0.0.5:8600/v1", allowed={"10.0.0.5"}, floor=set()))

    @override_settings(CONNECTIONS_ALLOWED_HOSTS=["10.0.0.5:8500"])
    def test_the_stack_may_allow_a_host_for_every_project(self):
        floor = set(hosts.floor_hosts())
        self.assertIn("10.0.0.5:8500", floor)
        self.assertIsNone(
            hosts.refusal_for("http://10.0.0.5:8500/v1", allowed=set(), floor=floor)
        )
        self.assertIsNotNone(
            hosts.refusal_for("http://10.0.0.6:8500/v1", allowed=set(), floor=floor)
        )

    def test_loopback_is_never_allowed_even_when_it_is_listed(self):
        for address in (
            "http://127.0.0.1:8000/api",
            "http://localhost:8000/",
            "http://[::1]:8000/",
            "http://0.0.0.0:8000/",
        ):
            listed = {"127.0.0.1:8000", "127.0.0.1", "localhost", "[::1]:8000", "0.0.0.0"}
            refusal = hosts.refusal_for(address, allowed=listed, floor=listed)
            self.assertIsNotNone(refusal, address)
            self.assertEqual(refusal[0], "loopback", address)

    def test_metadata_is_never_allowed_even_when_it_is_listed(self):
        for address in (
            "http://169.254.169.254/latest/meta-data/",
            "http://metadata.google.internal/computeMetadata/v1/",
        ):
            host = hosts.hostname_of(address)
            refusal = hosts.refusal_for(address, allowed={host}, floor={host})
            self.assertIsNotNone(refusal, address)
            self.assertEqual(refusal[0], "metadata", address)

    def test_the_deployments_own_services_are_never_allowed(self):
        refusal = hosts.refusal_for("http://aisc-eval-worker:8000/", allowed=set(), floor=set())
        self.assertIsNotNone(refusal)
        self.assertEqual(refusal[0], "deployment_service")
        listed = hosts.refusal_for(
            "http://immudb:3322/", allowed={"immudb:3322"}, floor={"immudb:3322"}
        )
        self.assertEqual(listed[0], "deployment_service")

    @override_settings(CONNECTIONS_STACK_HOSTS=["the-platform.internal"])
    def test_the_stack_hosts_may_be_configured(self):
        self.assertIn("the-platform.internal", hosts.stack_hosts())
        refusal = hosts.refusal_for("http://the-platform.internal/", allowed=set(), floor=set())
        self.assertEqual(refusal[0], "deployment_service")

    def test_an_address_with_no_host_is_refused(self):
        for address in ("", "   ", "/relative/path"):
            refusal = hosts.refusal_for(address, allowed=set(), floor=set())
            self.assertIsNotNone(refusal, address)
            self.assertEqual(refusal[0], "invalid_url", address)


class EntryNormalisationTest(SimpleTestCase):

    def test_an_entry_is_stored_lower_cased_without_scheme_or_path(self):
        self.assertEqual(hosts.normalize_entry(" HTTPS://Box.Local:8500/ask "), "box.local:8500")
        self.assertEqual(hosts.normalize_entry("box.local"), "box.local")
        self.assertEqual(hosts.normalize_entry("10.0.0.5:8500"), "10.0.0.5:8500")

    def test_an_entry_that_names_nothing_is_refused(self):
        for entry in ("", "   ", "http://", "a b", "host/path"):
            self.assertIsNone(hosts.normalize_entry(entry), entry)

    def test_a_hopeless_entry_is_refused_with_its_reason(self):
        self.assertIn("loopback", hosts.is_never_allowed("127.0.0.1"))
        self.assertIn("metadata", hosts.is_never_allowed("169.254.169.254"))
        self.assertIn("own services", hosts.is_never_allowed("redis"))
        self.assertIsNone(hosts.is_never_allowed("box.local:8500"))

    def test_saving_a_hopeless_address_is_refused(self):
        with self.assertRaises(HttpError) as caught:
            hosts.refuse_never_allowed("http://127.0.0.1:8000/v1")
        self.assertEqual(caught.exception.status_code, 400)
        hosts.refuse_never_allowed("http://box.local:8500/v1")  # a LAN host is fine to save
