"""
Test-suite setup: run hermetic by mocking the immudb client everywhere.

Routers call ``log_action`` (best-effort audit) on many endpoints; without a
live immudb each call printed ``audit log failed ...`` noise and, worse, the
old integration tests silently skipped. Patching the clerk's client class here
means every test in the suite exercises the audit path against the in-memory
fake instead of dialing localhost:3322.
"""
from unittest import mock

from aisc_backend.tests.immudb.fake_immudb import FakeImmudbClient

mock.patch("aisc_backend.audit.clerk.ImmudbClient", FakeImmudbClient).start()
