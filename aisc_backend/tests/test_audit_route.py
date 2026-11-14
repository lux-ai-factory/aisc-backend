"""An audit event written through the API names the app that wrote it from the caller's token (the Keycloak
client that issued it), never from the body; and the body is bounded and takes no other field."""
from types import SimpleNamespace
from unittest import mock

from django.test import SimpleTestCase
from pydantic import ValidationError

from aisc_backend.routers import audit


class TheAuditRouteTestCase(SimpleTestCase):
    def test_the_source_app_is_the_tokens_client_never_the_body(self):
        request = SimpleNamespace(aisc_claims={"azp": "aisc-gateway"}, auth={"preferred_username": "alice"},
                                  META={})
        with mock.patch.object(audit.clerk, "write_event") as write:
            try:
                event = audit.AuditEventIn(action="update", resource_type="checklist")
            except ValidationError as exc:
                self.fail(f"a body without source_app is refused today: {exc.errors()[0]['msg']}")
            audit.post_audit(request, event)
        self.assertEqual(write.call_args.kwargs["source_app"], "aisc-gateway")

    def test_a_body_naming_its_source_app_is_refused(self):
        with self.assertRaises(ValidationError):
            audit.AuditEventIn.model_validate({"action": "a", "resource_type": "r", "source_app": "controls"})

    def test_an_unknown_field_is_refused(self):
        with self.assertRaises(ValidationError):
            audit.AuditEventIn.model_validate({"action": "a", "resource_type": "r", "actor": "mallory"})

    def test_text_fields_are_bounded_and_the_outcome_is_ok_or_failed(self):
        for body in ({"action": "a" * 65, "resource_type": "r"}, {"action": "a", "resource_type": "r" * 65},
                     {"action": "a", "resource_type": "r", "resource_id": "x" * 256},
                     {"action": "a", "resource_type": "r", "outcome": "maybe"}):
            with self.subTest(body={k: (v[:10] + "...") for k, v in body.items()}):
                with self.assertRaises(ValidationError):
                    audit.AuditEventIn.model_validate(body)
