"""The audit's "where from" is where the person was, never the address of a container of the stack.

A person reaches the engine through the gateway (Caddy), which passes the address it saw in X-Real-IP and
X-Forwarded-For: that address is recorded, not the gateway's own. A call the stack's worker makes on the
internal routes has no person behind it: it is not recorded as if a person had acted from the worker's
container address."""
from types import SimpleNamespace
from unittest import mock

from django.test import SimpleTestCase

from aisc_backend.audit import log

GATEWAY = "172.18.0.9"
WORKER = "172.18.0.12"
PERSON = "203.0.113.7"


def _request(path, **meta):
    return SimpleNamespace(path=path, META=meta, auth=None)


def _recorded(request) -> dict:
    with mock.patch.object(log, "clerk") as clerk:
        log.log_action(request, action="upload_artifact", resource_type="evaluation", resource_id="e1")
    clerk.write_event.assert_called_once()
    return clerk.write_event.call_args.kwargs


class TheAuditRecordsWhereThePersonWas(SimpleTestCase):
    def test_a_person_through_the_gateway_is_recorded_by_the_address_the_gateway_saw(self):
        got = _recorded(_request("/api/v1/projects", REMOTE_ADDR=GATEWAY, HTTP_X_REAL_IP=PERSON,
                                 HTTP_X_FORWARDED_FOR=PERSON))
        self.assertEqual(got["source_ip"], PERSON)

    def test_the_gateways_real_ip_header_is_taken_over_its_own_address(self):
        got = _recorded(_request("/api/v1/projects", REMOTE_ADDR=GATEWAY, HTTP_X_REAL_IP=PERSON))
        self.assertEqual(got["source_ip"], PERSON, "the gateway's own container address is recorded")

    def test_a_call_of_the_stacks_worker_is_not_recorded_as_where_a_person_was(self):
        got = _recorded(_request("/api/v1/internal/evaluations/e1/artifacts", REMOTE_ADDR=WORKER,
                                 HTTP_X_INTERNAL_SECRET="fake-internal-secret"))
        self.assertNotEqual(got["source_ip"], WORKER,
                            "the worker's container address is recorded as where the action came from")
