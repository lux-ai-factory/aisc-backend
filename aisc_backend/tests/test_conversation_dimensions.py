"""A measure keeps a reference to the recorded call, not the conversation text: the values of a measurement's
prompt, response, conversation, answer, reply and request dimensions (any case) are replaced with
`trace:<test>#sha256=<digest of the text>`, which leads to the call in the platform's run trace. Every other
dimension stays as the plugin sent it, and an empty value stays empty."""
import hashlib
import importlib.util
import json
import uuid

from django.test import SimpleTestCase

RUN = uuid.UUID("11111111-2222-4333-8444-555555555555")


def _referenced():
    found = importlib.util.find_spec("aisc_backend.conversation_dimensions")
    assert found is not None, "the engine stores conversation text in a measure's dimensions as it is sent"
    from aisc_backend.conversation_dimensions import referenced
    return referenced


class ConversationDimensions(SimpleTestCase):
    def test_conversation_text_becomes_a_trace_reference(self):
        out = _referenced()({"concern": "gender", "Prompt": "hi there", "response": "hello", "expected": "x"}, RUN)
        self.assertEqual(out["Prompt"], f"trace:{RUN}#sha256={hashlib.sha256(b'hi there').hexdigest()}")
        self.assertEqual(out["response"], f"trace:{RUN}#sha256={hashlib.sha256(b'hello').hexdigest()}")
        self.assertEqual(out["concern"], "gender")
        self.assertEqual(out["expected"], "x")
        self.assertNotIn("hello", json.dumps(out))

    def test_every_conversation_key_is_referenced_whatever_its_case(self):
        dims = {k: "text" for k in ("PROMPT", "Response", "conversation", "Answer", "reply", "request")}
        out = _referenced()(dims, RUN)
        self.assertEqual({k for k, v in out.items() if v == "text"}, set())

    def test_an_empty_value_stays_empty(self):
        self.assertEqual(_referenced()({"prompt": ""}, uuid.uuid4())["prompt"], "")
