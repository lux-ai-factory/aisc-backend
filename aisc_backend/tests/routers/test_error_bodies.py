"""Every error body of the global exception handlers carries "detail", as HttpError and validation errors do.

The not-found and conflict bodies keep "message" and add "detail" with the same text. A server error
answers a fixed text under both keys: the exception's own text goes to the log only, never to the caller.
"""
from django.core.exceptions import MultipleObjectsReturned
from django.test import SimpleTestCase
from ninja import NinjaAPI, Router
from ninja.testing import TestClient

from aisc_backend.models.evaluation import Evaluation
from aisc_backend.utils.exception_handlers import register_exception_handlers
from config.settings import APP_NAME

INTERNAL_SERVER_ERROR = "Internal server error"
SECRET_LOOKING_TEXT = "secret-looking text"

raising = Router()


@raising.get("/missing")
def missing(request):
    raise Evaluation.DoesNotExist("Evaluation matching query does not exist.")


@raising.get("/many")
def many(request):
    raise MultipleObjectsReturned("many")


@raising.get("/boom")
def boom(request):
    raise RuntimeError(SECRET_LOOKING_TEXT)


api = NinjaAPI(urls_namespace="error-bodies-test")
register_exception_handlers(api)
api.add_router("/t", raising)

client = TestClient(api)


class GlobalErrorBodiesTestCase(SimpleTestCase):

    def assertDetailBesideMessage(self, response, status, starts_with, ends_with):
        self.assertEqual(status, response.status_code, response.content)
        body = response.json()
        self.assertEqual({"message", "detail"}, set(body))
        self.assertEqual(body["message"], body["detail"])
        self.assertTrue(body["detail"].startswith(starts_with), body["detail"])
        self.assertTrue(body["detail"].endswith(ends_with), body["detail"])

    def test_not_found_carries_detail_and_message(self):
        response = client.get("/t/missing")
        self.assertDetailBesideMessage(
            response, 404, "Evaluation matching query does not exist.", "GET /t/missing")

    def test_conflict_carries_detail_and_message(self):
        response = client.get("/t/many")
        self.assertDetailBesideMessage(response, 409, "many", "GET /t/many")

    def test_server_error_answers_a_fixed_text_and_logs_the_exception(self):
        with self.assertLogs(APP_NAME, level="ERROR") as logged:
            response = client.get("/t/boom")
        self.assertEqual(500, response.status_code, response.content)
        self.assertEqual({"message": INTERNAL_SERVER_ERROR, "detail": INTERNAL_SERVER_ERROR}, response.json())
        self.assertNotIn(SECRET_LOOKING_TEXT, response.content.decode())
        self.assertIn(SECRET_LOOKING_TEXT, "\n".join(logged.output))
        self.assertIn("Traceback", "\n".join(logged.output))
