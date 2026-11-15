"""The engine asks the worker for a plugin's manifest and for its form hooks on a queue of their own, which a
worker process of its own serves, so a configure page never waits behind the runs. A run still goes to the
worker's default queue."""
import unittest.mock as mock

from django.test import SimpleTestCase

from aisc_backend import plugin_declarations
from aisc_backend.services import celery_service

DECLARATIONS_QUEUE = "plugin-declarations"


def _sent(call):
    send = mock.Mock(return_value=mock.Mock(get=mock.Mock(return_value={})))
    with mock.patch.object(celery_service.celery, "send_task", new=send):
        call()
    send.assert_called_once()
    return send.call_args


class TheDeclarationsQueue(SimpleTestCase):
    def test_a_manifest_read_is_sent_to_the_declarations_queue(self):
        sent = _sent(lambda: plugin_declarations.fetch_manifest("demo-pkg", "1.0", None))
        self.assertEqual(sent.args, (plugin_declarations.READ_MANIFEST_TASK,))
        self.assertEqual(sent.kwargs.get("queue"), DECLARATIONS_QUEUE)

    def test_a_form_hook_is_sent_to_the_declarations_queue(self):
        row = mock.Mock(package_name="demo-pkg", version="1.0", wheel_sha256=None)
        row.name = "Demo"
        sent = _sent(lambda: plugin_declarations.plugin_hook(row, "on_config_change", {"config": None}))
        self.assertEqual(sent.args, (plugin_declarations.PLUGIN_HOOK_TASK,))
        self.assertEqual(sent.kwargs.get("queue"), DECLARATIONS_QUEUE)

    def test_a_run_keeps_the_default_queue(self):
        """test_celery_dispatch pins a run's message: run_evaluation with its arguments and no queue."""
        self.assertFalse(celery_service.celery.conf.task_routes)
        self.assertNotEqual(celery_service.celery.conf.task_default_queue, DECLARATIONS_QUEUE)
