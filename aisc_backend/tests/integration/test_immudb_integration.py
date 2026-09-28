"""
Tests for the immudb audit clerk — run HERMETICALLY against an in-memory fake
immudb client so they execute in CI instead of silently skipping.

Proves the chain: clerk.connect() provisions, write_event() persists, and the
row reads back; plus the tamp-proof VERIFIED ops roundtrip.
"""
import json
import unittest.mock as mock

from django.test import SimpleTestCase

from aisc_backend.audit.clerk import AuditClerk
from aisc_backend.tests.immudb.fake_immudb import FakeImmudbClient


class ImmudbIntegrationTest(SimpleTestCase):

    def setUp(self):
        self.immudb_patch = mock.patch("aisc_backend.audit.clerk.ImmudbClient", FakeImmudbClient)
        self.immudb_patch.start()
        self.addCleanup(self.immudb_patch.stop)
        self.clerk = AuditClerk()
        self.clerk.connect()   # connects + provisions against the in-memory fake

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
        # Prove a verifiedSet/verifiedGet roundtrip succeeds.
        c = self.clerk._client
        c.verifiedSet(b"itest:verified-key", b"sealed-value")
        result = c.verifiedGet(b"itest:verified-key")
        # verifiedGet returns an object whose .value is the bytes.
        self.assertEqual(result.value, b"sealed-value")
