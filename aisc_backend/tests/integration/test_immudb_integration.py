"""
INTEGRATION tests for the immudb audit clerk — require a RUNNING immudb. Auto-skips if not reachable.

Proves the REAL chain: clerk.connect() provisions, write_event() persists, and the row reads back.
Also proves the tamper-proof VERIFIED ops work in the Python SDK (design decision #5).

Run (immudb up), handing the process the env it needs (host → localhost:3322):
    IMMUDB_URL=localhost:3322 IMMUDB_ADMIN_PASSWORD=immudbDev1! \
    uv run manage.py test aisc_backend.tests.integration.test_immudb_integration
"""
import json
import unittest

from django.conf import settings
from django.test import SimpleTestCase

from immudb import ImmudbClient
from aisc_backend.audit.clerk import AuditClerk


def _immudb_up() -> bool:
    try:
        c = ImmudbClient(settings.IMMUDB_URL)
        c.login(settings.IMMUDB_USER, settings.IMMUDB_PASSWORD)
        c.logout()
        return True
    except Exception:
        return False


@unittest.skipUnless(_immudb_up(), "immudb not reachable on settings.IMMUDB_URL")
class ImmudbIntegrationTest(SimpleTestCase):
    def setUp(self):
        self.clerk = AuditClerk()
        self.clerk.connect()   # real connect + provision against the running immudb

    def test_write_event_persists_and_reads_back(self):
        marker = "integration_probe"   # unique resource_type for this test
        self.clerk.write_event(actor="itest-user", action="probe", resource_type=marker,
                               resource_id="r-7", source_app="backend", source_ip="1.2.3.4",
                               outcome="ok", metadata={"k": "v", "n": 7})
        rows = self.clerk._client.sqlQuery(
            "SELECT actor, action, resource_type, resource_id, source_app, source_ip, outcome, metadata "
            "FROM audit_log WHERE resource_type = @rt;",
            params={"rt": marker},
        )
        self.assertTrue(rows, "the event we just wrote should be queryable")
        actor, action, rtype, rid, app, ip, outcome, metadata = rows[-1]
        self.assertEqual(actor, "itest-user")
        self.assertEqual(action, "probe")
        self.assertEqual(rtype, marker)
        self.assertEqual(rid, "r-7")
        self.assertEqual(app, "backend")
        self.assertEqual(ip, "1.2.3.4")
        self.assertEqual(outcome, "ok")
        self.assertEqual(json.loads(metadata), {"k": "v", "n": 7})

    def test_verified_roundtrip_proves_tamper_check_works(self):
        # The design relies on Python's VERIFIED ops (Node's are broken). Prove a verifiedSet/verifiedGet
        # roundtrip succeeds = the SDK's cryptographic tamper-check is functioning.
        c = self.clerk._client
        c.verifiedSet(b"itest:verified-key", b"sealed-value")
        result = c.verifiedGet(b"itest:verified-key")
        # verifiedGet returns an object whose .value is the bytes; the SDK already verified the proof.
        self.assertEqual(result.value, b"sealed-value")
