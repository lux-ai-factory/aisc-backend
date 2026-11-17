"""
Adapter unit tests (CON-01), with no network: each protocol's request builder and
response parser, the REST templating and refusal matching, the JSON path reader,
and the A2A card URL.
"""
from django.test import SimpleTestCase

from aisc_backend import adapters
from aisc_backend.adapters.base import Secret, json_path, render
from aisc_backend.models.connection import Connection


def _connection(**fields) -> Connection:
    """An unsaved Connection: the adapters only read fields, never the database."""
    defaults = {"description": "", "project_id": 1}
    defaults.update(fields)
    return Connection(**defaults)


class RestAdapterTest(SimpleTestCase):
    def test_builds_the_templated_request(self):
        connection = _connection(
            kind="rest", base_url="https://system.example.com/", path="/chat",
            method="post", body_template={"question": "{{input}}", "history": "{{history}}"},
            headers={"X-Client": "aisc"},
        )
        request = adapters.ADAPTERS["rest"].build_request(
            connection, Secret(key="k1", headers={"X-Token": "t"}),
            input="why?", history=[{"role": "user", "content": "hi"}],
        )
        self.assertEqual(request.method, "POST")
        self.assertEqual(request.url, "https://system.example.com/chat")
        self.assertEqual(request.json_body["question"], "why?")
        self.assertEqual(request.json_body["history"], [{"role": "user", "content": "hi"}])
        self.assertEqual(request.headers["X-Client"], "aisc")
        self.assertEqual(request.headers["Authorization"], "Bearer k1")
        self.assertEqual(request.headers["X-Token"], "t")

    def test_reads_the_answer_at_the_response_path(self):
        connection = _connection(kind="rest", response_path="data.items[0].text")
        answer = adapters.ADAPTERS["rest"].parse_response(
            200, '{"data": {"items": [{"text": "the answer"}]}}', connection,
        )
        self.assertTrue(answer.ok)
        self.assertEqual(answer.answer, "the answer")

    def test_a_matching_status_and_body_is_a_refusal_not_an_error(self):
        connection = _connection(
            kind="rest", response_path="detail.reason",
            refusal={"status": [502], "path": "detail.reason",
                     "match": {"detail.error": "answer_not_grounded"}},
        )
        answer = adapters.ADAPTERS["rest"].parse_response(
            502, '{"detail": {"error": "answer_not_grounded", "reason": "ungrounded"}}', connection,
        )
        self.assertTrue(answer.ok)
        self.assertTrue(answer.refused)
        self.assertEqual(answer.refusal_reason, "ungrounded")

    def test_a_non_matching_body_is_an_error(self):
        connection = _connection(kind="rest", response_path="answer")
        answer = adapters.ADAPTERS["rest"].parse_response(500, "upstream exploded", connection)
        self.assertFalse(answer.ok)
        self.assertEqual(answer.error, "http_status")


class OpenAiAdapterTest(SimpleTestCase):
    def test_builds_a_chat_completion(self):
        connection = _connection(kind="openai", base_url="https://llm.example.com/v1", model="gpt-4o-mini")
        request = adapters.ADAPTERS["openai"].build_request(
            connection, Secret(key="sk"), input="hello",
        )
        self.assertEqual(request.url, "https://llm.example.com/v1/chat/completions")
        self.assertEqual(request.json_body["model"], "gpt-4o-mini")
        self.assertEqual(request.json_body["messages"][-1], {"role": "user", "content": "hello"})
        self.assertEqual(request.headers["Authorization"], "Bearer sk")

    def test_reads_content_and_capabilities(self):
        connection = _connection(kind="openai")
        body = '{"choices": [{"message": {"content": "hi", "tool_calls": [{"id": "1"}]}, "logprobs": {"a": 1}}]}'
        answer = adapters.ADAPTERS["openai"].parse_response(200, body, connection)
        self.assertTrue(answer.ok)
        self.assertEqual(answer.answer, "hi")
        self.assertTrue(answer.capabilities["tools"])
        self.assertTrue(answer.capabilities["logprobs"])


class A2aAdapterTest(SimpleTestCase):
    def test_builds_a_json_rpc_message_send(self):
        connection = _connection(
            kind="a2a", base_url="https://agent.example.com", path="/a2a", protocol_version="1.0",
        )
        request = adapters.ADAPTERS["a2a"].build_request(connection, Secret(), input="hi")
        self.assertEqual(request.url, "https://agent.example.com/a2a")
        self.assertEqual(request.json_body["method"], "message/send")
        self.assertEqual(request.json_body["params"]["message"]["parts"][0]["text"], "hi")

    def test_reads_the_artifact_text(self):
        connection = _connection(kind="a2a")
        body = '{"jsonrpc": "2.0", "result": {"artifacts": [{"parts": [{"kind": "text", "text": "42"}]}]}}'
        answer = adapters.ADAPTERS["a2a"].parse_response(200, body, connection)
        self.assertTrue(answer.ok)
        self.assertEqual(answer.answer, "42")

    def test_an_rpc_error_is_reported(self):
        connection = _connection(kind="a2a")
        body = '{"jsonrpc": "2.0", "error": {"code": -32601, "message": "no such method"}}'
        answer = adapters.ADAPTERS["a2a"].parse_response(200, body, connection)
        self.assertFalse(answer.ok)
        self.assertEqual(answer.error, "rpc_error")
        self.assertEqual(answer.detail, "no such method")

    def test_the_card_url_follows_the_version(self):
        self.assertEqual(
            adapters.agent_card_url("https://agent.example.com/", "1.0"),
            "https://agent.example.com/.well-known/agent-card.json",
        )
        self.assertEqual(
            adapters.agent_card_url("https://agent.example.com", "0.3"),
            "https://agent.example.com/.well-known/agent.json",
        )


class OipAdapterTest(SimpleTestCase):
    def test_builds_an_infer_call(self):
        connection = _connection(kind="oip", base_url="https://kserve.example.com", model="m1")
        request = adapters.ADAPTERS["oip"].build_request(connection, Secret(), input="ping")
        self.assertEqual(request.url, "https://kserve.example.com/v2/models/m1/infer")
        self.assertEqual(request.json_body["inputs"][0]["datatype"], "BYTES")

    def test_decodes_a_bytes_output(self):
        import base64

        connection = _connection(kind="oip")
        encoded = base64.b64encode(b"decoded answer").decode()
        body = '{"outputs": [{"name": "text", "datatype": "BYTES", "data": ["%s"]}]}' % encoded
        answer = adapters.ADAPTERS["oip"].parse_response(200, body, connection)
        self.assertTrue(answer.ok)
        self.assertEqual(answer.answer, "decoded answer")


class HelpersTest(SimpleTestCase):
    def test_render_replaces_a_whole_placeholder_with_the_value(self):
        rendered = render({"applicant": "{{params.applicant}}"}, {"params": {"applicant": {"id": 7}}})
        self.assertEqual(rendered, {"applicant": {"id": 7}})

    def test_json_path_walks_keys_and_indexes(self):
        self.assertEqual(json_path({"a": {"b": [1, {"c": "x"}]}}, "a.b[1].c"), "x")
        self.assertIsNone(json_path({"a": 1}, "a.b.c"))

    def test_a_secret_header_line_replaces_the_key(self):
        connection = _connection(kind="rest", secret_header="X-Token: {{secret}}")
        from aisc_backend.adapters.base import secret_headers

        headers = secret_headers(connection, Secret(key="abc"))
        self.assertEqual(headers, {"X-Token": "abc"})

    def test_the_registry_serves_the_four_built_in_protocols(self):
        self.assertEqual(
            sorted(adapters.ADAPTERS),
            ["a2a", "oip", "openai", "rest"],
        )
        self.assertEqual(len(adapters.kinds()), 4)
