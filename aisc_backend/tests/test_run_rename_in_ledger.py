"""Renaming a run is a change the ledger records: one event naming the run's name before and after, citing the
person's request, written in the same transaction as the new name, so a rename whose event cannot be written
is not made."""
import unittest.mock as mock

from asgiref.sync import sync_to_async
from ninja.testing import TestAsyncClient

from aisc_backend import ledger
from aisc_backend.auth import keycloak
from aisc_backend.models import EvaluationPlugin
from aisc_backend.routers import evaluation as evaluation_routes
from aisc_backend.tests.run_ledger_support import REQUEST, RunRecording, events_of, unrecorded

SIGNED_IN = {"Authorization": "Bearer development"}
evaluations = TestAsyncClient(evaluation_routes.router)


class RunRenameInLedger(RunRecording):
    def setUp(self):
        super().setUp()
        auth_off = mock.patch.object(keycloak, "AUTH_ENABLED", False)
        auth_off.start()
        self.addCleanup(auth_off.stop)
        with unrecorded():
            EvaluationPlugin.objects.filter(pk=self.evaluation_plugin.pk).update(name="first name")

    async def rename(self, name):
        with mock.patch.object(evaluation_routes, "log_action"):
            return await evaluations.patch(f"/{self.evaluation.pid}/name", headers=SIGNED_IN, json={"name": name})

    async def names(self):
        return await sync_to_async(list)(EvaluationPlugin.objects.filter(evaluation=self.evaluation)
                                         .values_list("name", flat=True))

    async def test_renaming_a_run_is_recorded_with_its_names_before_and_after(self):
        res = await self.rename("Local back-end, 4 Nov")
        self.assertEqual(res.status_code, 200, res.content)
        renamed = await events_of("engine.evaluation.renamed")
        self.assertEqual(len(renamed), 1, "no rename event in the ledger outbox")
        [event] = renamed
        self.assertEqual((event["item_type"], event["item_id"], event["request_id"]),
                         ("evaluation", str(self.evaluation.pid), REQUEST))
        self.assertEqual(event["details"], {"name_before": "first name", "name_after": "Local back-end, 4 Nov"})

    async def test_a_rename_whose_event_cannot_be_written_is_not_made(self):
        with mock.patch.object(ledger, "emit", side_effect=RuntimeError("the outbox is full")):
            try:
                await self.rename("never kept")
            except RuntimeError:
                pass
        self.assertEqual(await self.names(), ["first name"])
