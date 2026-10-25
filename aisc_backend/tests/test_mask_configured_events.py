"""The engine's outbox keeps no plaintext plugin secret (aisc_backend.secret_cleanup.mask_outbox, the outbox step
of `manage.py clean_old_secrets apply`), and an undelivered event is masked when it is delivered, before the
cleanup has run.

Rows, one project, plugin aisc-plugin-langbite with pid PL: r1 configured and delivered, holding the judge key;
r2 configured and undelivered; r3 configured and delivered, already masked; r4 a toggle; r5 configured and refused.
"""
import copy
import importlib
import io
import json
import logging
import uuid
from datetime import datetime, timezone

from django.test import TestCase

from aisc_backend import ledger
from aisc_backend.models import LedgerOutbox, Plugin, Project

KEY = "lb-judge-0123456789abcdef"
R2_SECRET = "abcdef1234567890"
PID = "0f0e0d0c-0b0a-4908-8706-050403020101"
PL = "6a1c2b3d-4e5f-4a6b-8c7d-9e0f1a2b3c4d"
SETTINGS = {"api_key": "0b1c2d3e-4f50-4617-8293-a4b5c6d7e8f9"}


def cleanup():
    return importlib.import_module("aisc_backend.secret_cleanup")


def configured(config, **content):
    return {"event_id": str(uuid.uuid4()), "request_id": str(uuid.uuid4()), "action": "engine.plugin.configured",
            "item_type": "plugin", "item_id": PL, "details": {},
            "content": {"config_id": 1, "config": config, "settings": {}, **content}}


class OutboxRows(TestCase):
    def setUp(self):
        project = Project.objects.create(name="p", description="")
        Plugin.objects.create(project=project, name="LangBiTe", display_name="LangBiTe",
                              package_name="aisc-plugin-langbite", version="0.2.8", pid=PL)
        now = datetime.now(timezone.utc)
        self.r1 = LedgerOutbox.objects.create(project_pid=PID, delivered_at=now, event=configured(
            {"judge_api_key": KEY, "languages": ["en"]}, settings=SETTINGS))
        self.r2 = LedgerOutbox.objects.create(project_pid=PID, event=configured(
            {"openai_token": R2_SECRET, "temperature": 0.2}))
        self.r3 = LedgerOutbox.objects.create(project_pid=PID, delivered_at=now, event=configured(
            {"judge_api_key": "set"}))
        self.r4 = LedgerOutbox.objects.create(project_pid=PID, delivered_at=now, event={
            "event_id": str(uuid.uuid4()), "request_id": str(uuid.uuid4()), "action": "engine.plugin.toggled",
            "item_type": "plugin", "item_id": PL, "details": {"enabled": False}})
        self.r5 = LedgerOutbox.objects.create(project_pid=PID, refused=409, event=configured({"password": "pw"}))
        self.rows = [self.r1, self.r2, self.r3, self.r4, self.r5]
        self.before = {row.pk: (copy.deepcopy(row.event), row.queued_at, row.delivered_at, row.refused)
                       for row in self.rows}

    def apply(self):
        return cleanup().mask_outbox(LedgerOutbox, "default", apply=True)

    def stored(self, row):
        return LedgerOutbox.objects.get(pk=row.pk)

    def test_apply_masks_every_configured_row_and_touches_nothing_else(self):
        self.apply()
        r1 = self.stored(self.r1)
        self.assertEqual(r1.event["content"]["config"], {"judge_api_key": "set", "languages": ["en"]})
        self.assertEqual(r1.event["content"]["settings"], SETTINGS)
        self.assertEqual(r1.event["content"]["config_id"], 1)
        self.assertEqual(self.stored(self.r2).event["content"]["config"], {"openai_token": "set", "temperature": 0.2})
        self.assertEqual(self.stored(self.r5).event["content"]["config"], {"password": "set"})
        self.assertEqual(self.stored(self.r3).event, self.before[self.r3.pk][0])
        self.assertEqual(self.stored(self.r4).event, self.before[self.r4.pk][0])
        for row in self.rows:
            now = self.stored(row)
            event, queued_at, delivered_at, refused = self.before[row.pk]
            self.assertEqual((now.event["event_id"], now.event["action"], now.queued_at, now.delivered_at,
                              now.refused), (event["event_id"], event["action"], queued_at, delivered_at, refused))

    def test_an_undelivered_row_is_delivered_masked_before_any_cleanup(self):
        sent = []
        ledger.deliver("default", lambda pid, event: sent.append(copy.deepcopy(event)) or 202)
        self.assertEqual([event["event_id"] for event in sent], [self.r2.event["event_id"]])
        self.assertEqual(sent[0]["content"]["config"], {"openai_token": "set", "temperature": 0.2})
        self.assertNotIn(R2_SECRET, json.dumps(sent))

    def test_after_the_cleanup_the_undelivered_row_is_sent_masked(self):
        self.apply()
        sent = []
        ledger.deliver("default", lambda pid, event: sent.append(copy.deepcopy(event)) or 202)
        self.assertEqual([event["event_id"] for event in sent], [self.r2.event["event_id"]])
        self.assertEqual(sent[0]["content"]["config"], {"openai_token": "set", "temperature": 0.2})
        self.assertNotIn(R2_SECRET, json.dumps(sent))

    def test_count_names_the_fields_to_rotate_and_changes_nothing(self):
        found = cleanup().mask_outbox(LedgerOutbox, "default", apply=False)
        self.assertEqual(found, (3, [(PL, "judge_api_key"), (PL, "openai_token"), (PL, "password")]))
        for row in self.rows:
            self.assertEqual(self.stored(row).event, self.before[row.pk][0])

    def test_a_second_apply_changes_nothing(self):
        self.apply()
        self.assertEqual(self.apply(), (0, []))

    def test_no_value_is_logged_and_the_fields_to_rotate_are(self):
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        records = []

        class Keep(logging.Handler):
            def emit(self, record):
                records.append(record)

        keep = Keep()
        root = logging.getLogger()
        root.addHandler(handler)
        root.addHandler(keep)
        previous = root.level
        root.setLevel(logging.DEBUG)
        try:
            self.apply()
        finally:
            root.removeHandler(handler)
            root.removeHandler(keep)
            root.setLevel(previous)
        self.assertNotIn(KEY, stream.getvalue())
        self.assertNotIn(R2_SECRET, stream.getvalue())
        warnings = [record.getMessage() for record in records if record.levelno == logging.WARNING]
        self.assertIn("rotate plugin=aisc-plugin-langbite field=judge_api_key", warnings)
