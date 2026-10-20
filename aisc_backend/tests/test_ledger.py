"""The engine's outbox for the platform's ledger (aisc_backend.ledger)."""
import json
import os
import re
import uuid
from unittest import mock

from asgiref.sync import sync_to_async
from django.db import transaction
from django.test import RequestFactory, SimpleTestCase, TestCase

from aisc_backend import ledger, projectdb
from aisc_backend.models import LedgerOutbox, Project, ProjectStatus

PID = "0f0e0d0c-0b0a-4908-8706-050403020100"
REQUEST = "11111111-2222-4333-8444-555555555555"
RECORD = {"LEDGER_MODE": "record"}
SENDER = {"PLATFORM_URL": "http://platform:8000", "PLATFORM_LEDGER_ENGINE_TOKEN": "engine-token"}


class InAProject:
    """The door has admitted PID and the gateway witnessed REQUEST."""

    def setUp(self):
        super().setUp()
        self._pid = projectdb.admitted_pid.set(PID)
        self._request = ledger.current_request.set(REQUEST)

    def tearDown(self):
        ledger.current_request.reset(self._request)
        projectdb.admitted_pid.reset(self._pid)
        super().tearDown()


class Emitting(InAProject, TestCase):
    def test_nothing_is_written_while_the_ledger_is_off(self):
        with mock.patch.dict(os.environ, {"LEDGER_MODE": "off"}):
            self.assertIsNone(ledger.emit("engine.component.deleted", item_type="component", item_id="c"))
        self.assertFalse(LedgerOutbox.objects.exists())

    def test_nothing_is_written_outside_a_project(self):
        projectdb.admitted_pid.set(None)
        with mock.patch.dict(os.environ, RECORD):
            self.assertIsNone(ledger.emit("engine.component.deleted", item_type="component", item_id="c"))
        self.assertFalse(LedgerOutbox.objects.exists())

    def test_an_event_cites_the_request_and_never_the_person(self):
        with mock.patch.dict(os.environ, RECORD):
            ledger.emit("engine.component.deleted", item_type="component", item_id=uuid.UUID(int=7),
                        details={"component_type": "dataset"})
        row = LedgerOutbox.objects.get()
        self.assertEqual(str(row.project_pid), PID)
        event = row.event
        uuid.UUID(event["event_id"])
        self.assertEqual(event["request_id"], REQUEST)
        self.assertEqual(event["action"], "engine.component.deleted")
        self.assertEqual(event["item_type"], "component")
        self.assertEqual(event["item_id"], str(uuid.UUID(int=7)))
        self.assertEqual(event["details"], {"component_type": "dataset"})
        self.assertEqual(set(event), {"event_id", "request_id", "action", "item_type", "item_id", "details"})

    def test_a_run_and_states_are_sent_when_given(self):
        with mock.patch.dict(os.environ, RECORD):
            ledger.emit("engine.plugin.configured", item_type="plugin", item_id="p", run_id=uuid.UUID(int=1),
                        content={"config": {"a": 1}})
        event = LedgerOutbox.objects.get().event
        self.assertEqual(event["run_id"], str(uuid.UUID(int=1)))
        self.assertEqual(event["content"], {"config": {"a": 1}})

    def test_a_rolled_back_change_leaves_no_event(self):
        with mock.patch.dict(os.environ, RECORD):
            try:
                with transaction.atomic():
                    ledger.emit("engine.component.deleted", item_type="component", item_id="c")
                    raise RuntimeError("the change failed")
            except RuntimeError:
                pass
        self.assertFalse(LedgerOutbox.objects.exists())

    def test_the_post_starts_after_the_commit(self):
        with mock.patch.dict(os.environ, {**RECORD, **SENDER}), mock.patch.object(ledger, "deliver_soon") as soon:
            with self.captureOnCommitCallbacks(execute=True):
                ledger.emit("engine.component.deleted", item_type="component", item_id="c")
                soon.assert_not_called()
        soon.assert_called_once_with("default")

    def test_a_process_without_the_engines_token_leaves_its_events_to_the_deliverer(self):
        """The backend imports plugin code, so it holds no ledger token: its events wait in the outbox for
        aisc-backend-ledger (manage.py deliver_ledger_events), and it does not try to send them itself."""
        for env in ({}, {"PLATFORM_URL": "http://platform:8000"}, {"PLATFORM_LEDGER_ENGINE_TOKEN": "t"}):
            with self.subTest(env=env), mock.patch.dict(os.environ, {**RECORD, **env}), \
                    mock.patch.object(ledger, "deliver_soon") as soon:
                for name in ("PLATFORM_URL", "PLATFORM_LEDGER_ENGINE_TOKEN"):
                    if name not in env:
                        os.environ.pop(name, None)
                with self.captureOnCommitCallbacks(execute=True):
                    ledger.emit("engine.component.deleted", item_type="component", item_id="c")
                soon.assert_not_called()


class Atomically(InAProject, TestCase):
    async def test_every_write_of_the_work_and_its_event_commit_together(self):
        async def work():
            await Project.objects.acreate(name="one", status=ProjectStatus.Created)
            await sync_to_async(ledger.emit)("engine.project.created", item_type="engine_project", item_id="x")
            raise RuntimeError("the second write failed")

        with mock.patch.dict(os.environ, RECORD):
            with self.assertRaises(RuntimeError):
                await ledger.atomically(work)
        self.assertFalse(await Project.objects.filter(name="one").aexists())
        self.assertFalse(await LedgerOutbox.objects.aexists())

    async def test_the_work_answers(self):
        async def work():
            return await Project.objects.acreate(name="two", status=ProjectStatus.Created)

        project = await ledger.atomically(work)
        self.assertTrue(await Project.objects.filter(pk=project.pk).aexists())


class RequestIds(TestCase):
    def test_every_request_passes_through_the_middleware(self):
        from django.conf import settings

        self.assertIn("aisc_backend.ledger.RequestId", settings.MIDDLEWARE)

    def test_only_a_uuid_is_kept(self):
        self.assertEqual(ledger.request_id_of(REQUEST.upper()), REQUEST)
        for value in (None, "", "not-a-uuid", "1; DROP TABLE"):
            self.assertIsNone(ledger.request_id_of(value))

    async def test_the_middleware_holds_the_request_id_while_the_view_runs(self):
        seen = []

        async def view(request):
            seen.append(ledger.current_request.get())
            return "answer"

        middleware = ledger.RequestId(view)
        request = RequestFactory().get("/api/v1/me", HTTP_X_AISC_REQUEST_ID=REQUEST)
        self.assertEqual(await middleware(request), "answer")
        await middleware(RequestFactory().get("/api/v1/me", HTTP_X_AISC_REQUEST_ID="forged"))
        self.assertEqual(seen, [REQUEST, None])
        self.assertIsNone(ledger.current_request.get())

    async def _seen_with(self, claims=None, refused=False, token="t"):
        """The request id the view sees, with sign-in on and the token verifying to `claims`."""
        seen = []

        async def view(request):
            seen.append(ledger.current_request.get())
            return "answer"

        def verify(_token):
            if refused:
                raise ValueError("not a token")
            return claims

        headers = {"HTTP_X_AISC_REQUEST_ID": REQUEST}
        if token:
            headers["HTTP_AUTHORIZATION"] = f"Bearer {token}"
        with mock.patch("aisc_backend.auth.keycloak.AUTH_ENABLED", True), \
                mock.patch("aisc_backend.auth.keycloak.verify_token", side_effect=verify):
            await ledger.RequestId(view)(RequestFactory().get("/api/v1/me", **headers))
        return seen[0]

    async def test_with_sign_in_on_the_id_is_kept_only_on_a_request_the_gateway_sent(self):
        self.assertEqual(await self._seen_with({"azp": "aisc-gateway"}), REQUEST)
        self.assertIsNone(await self._seen_with({"azp": "aisc-webapp"}))
        self.assertIsNone(await self._seen_with(refused=True))
        self.assertIsNone(await self._seen_with({"azp": "aisc-gateway"}, token=""))

    async def test_the_gateways_own_header_counts_as_its_token(self):
        seen = []

        async def view(request):
            seen.append(ledger.current_request.get())
            return "answer"

        request = RequestFactory().get("/api/v1/me", HTTP_X_AISC_REQUEST_ID=REQUEST,
                                       HTTP_X_AUTH_REQUEST_ACCESS_TOKEN="t")
        with mock.patch("aisc_backend.auth.keycloak.AUTH_ENABLED", True), \
                mock.patch("aisc_backend.auth.keycloak.verify_token", return_value={"azp": "aisc-gateway"}):
            await ledger.RequestId(view)(request)
        self.assertEqual(seen, [REQUEST])


class Delivering(InAProject, TestCase):
    def _queue(self, n):
        with mock.patch.dict(os.environ, RECORD):
            for i in range(n):
                ledger.emit("engine.component.deleted", item_type="component", item_id=str(i))
        return list(LedgerOutbox.objects.order_by("id"))

    def test_events_are_sent_in_order_and_marked(self):
        rows = self._queue(2)
        sent = []
        self.assertEqual(ledger.deliver("default", lambda pid, event: sent.append((pid, event)) or 202), (2, 0))
        self.assertEqual(sent, [(PID, rows[0].event), (PID, rows[1].event)])
        self.assertFalse(LedgerOutbox.objects.filter(delivered_at__isnull=True).exists())
        self.assertEqual(ledger.deliver("default", lambda pid, event: self.fail("sent twice")), (0, 0))

    def test_a_refused_event_is_kept_and_never_sent_again(self):
        rows = self._queue(2)
        answers = iter([422, 202])
        self.assertEqual(ledger.deliver("default", lambda pid, event: next(answers)), (1, 1))
        self.assertEqual(LedgerOutbox.objects.get(pk=rows[0].pk).refused, 422)
        self.assertIsNone(LedgerOutbox.objects.get(pk=rows[0].pk).delivered_at)
        self.assertIsNotNone(LedgerOutbox.objects.get(pk=rows[1].pk).delivered_at)
        self.assertEqual(ledger.deliver("default", lambda pid, event: self.fail("sent again")), (0, 0))

    def test_a_failure_keeps_the_event_and_the_later_ones_queued(self):
        self._queue(2)
        for answer in (503, None):
            calls = []
            self.assertEqual(ledger.deliver("default", lambda pid, event: calls.append(event) or answer), (0, 1))
            self.assertEqual(len(calls), 1)
        self.assertEqual(LedgerOutbox.objects.filter(delivered_at__isnull=True, refused__isnull=True).count(), 2)
        self.assertEqual(ledger.deliver("default", lambda pid, event: 202), (2, 0))


class Reply:
    def __init__(self, status, body=None):
        self.status = status
        self.body = json.dumps(body or {}).encode()


class Sending(TestCase):
    ENV = {"PLATFORM_URL": "http://platform:8000/", "PLATFORM_LEDGER_ENGINE_TOKEN": "engine-token"}

    def test_the_event_is_posted_to_the_internal_route_with_the_engines_token(self):
        posted = []

        def post(url, body, headers):
            posted.append((url, json.loads(body), headers))
            return Reply(202)

        with mock.patch.dict(os.environ, self.ENV):
            self.assertEqual(ledger.http_sender(post)(PID, {"event_id": "e"}), 202)
        url, body, headers = posted[0]
        self.assertEqual(url, f"http://platform:8000/internal/projects/{PID}/ledger/events")
        self.assertEqual(body, {"event_id": "e"})
        self.assertEqual(headers["X-AISC-Service-Token"], "engine-token")
        self.assertEqual(headers["Content-Type"], "application/json")

    def test_nothing_is_sent_without_the_url_or_the_token(self):
        for missing in ("PLATFORM_URL", "PLATFORM_LEDGER_ENGINE_TOKEN"):
            env = {**self.ENV, missing: ""}
            with mock.patch.dict(os.environ, env):
                self.assertIsNone(ledger.http_sender(lambda *a: self.fail("posted"))(PID, {}))

    def test_a_404_counts_only_when_the_platform_says_it_has_no_such_project(self):
        with mock.patch.dict(os.environ, self.ENV):
            self.assertEqual(ledger.http_sender(lambda *a: Reply(404, {"detail": f"no project '{PID}'"}))(PID, {}),
                             404)
            self.assertIsNone(ledger.http_sender(lambda *a: Reply(404, {"detail": "Not Found"}))(PID, {}))

    def test_an_unreachable_platform_is_none(self):
        def post(*args):
            raise OSError("connection refused")

        with mock.patch.dict(os.environ, self.ENV):
            self.assertIsNone(ledger.http_sender(post)(PID, {}))


class TheDeliveryCommand(InAProject, TestCase):
    def test_a_pass_sends_what_is_queued(self):
        from io import StringIO

        from django.core.management import call_command

        with mock.patch.dict(os.environ, RECORD):
            ledger.emit("engine.component.deleted", item_type="component", item_id="c")
        sent = []
        out = StringIO()
        with mock.patch.object(ledger, "http_sender", return_value=lambda pid, event: sent.append(event) or 202):
            call_command("deliver_ledger_events", stdout=out)
        self.assertEqual(len(sent), 1)
        self.assertIsNotNone(LedgerOutbox.objects.get().delivered_at)
        self.assertIn("1 sent", out.getvalue())

    def test_it_runs_until_stopped_when_asked_to_repeat(self):
        from django.core.management import call_command

        from aisc_backend.management.commands import deliver_ledger_events

        with mock.patch.object(deliver_ledger_events.time, "sleep", side_effect=[None, KeyboardInterrupt]), \
                mock.patch.object(ledger, "http_sender", return_value=lambda pid, event: 202), \
                mock.patch.object(deliver_ledger_events.Command, "make_pass") as make_pass:
            with self.assertRaises(KeyboardInterrupt):
                call_command("deliver_ledger_events", every=5)
        self.assertEqual(make_pass.call_count, 2)


class ThePlatformCanSeeWhatIsLeftToSend(SimpleTestCase):
    """The platform drops a project database only once the engine's outbox there is sent
    (platform_service.routes.projects._engine_left): it may read which rows are left, and nothing of them."""

    def test_the_grant_step_lets_platform_rw_count_the_unsent_rows_only(self):
        from aisc_backend.management.commands.migrate_projects import READER_GRANTS

        grant = re.search(r"GRANT SELECT \(([^)]*)\) ON engine\.aisc_backend_ledgeroutbox TO platform_rw", READER_GRANTS)
        self.assertIsNotNone(grant, "platform_rw is not granted the outbox's state")
        self.assertEqual({c.strip() for c in grant.group(1).split(",")}, {"id", "delivered_at", "refused"})
