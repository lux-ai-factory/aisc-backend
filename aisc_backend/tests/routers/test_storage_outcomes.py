"""
File-repository outcomes and the oversized-request handler.

A missing object is a 404; access failures and storage outages stay
server/upstream errors (never mislabelled as missing). Oversized request
bodies answer 413 through the explicit handler, not a generic 500.
"""
import unittest.mock as mock

from django.core.exceptions import RequestDataTooBig
from django.test import TestCase

from aisc_backend.models.common import StorageContainer
from aisc_backend.repositories import file_repository


def _client_error(code: str, op: str = "GetObject"):
    from botocore.exceptions import ClientError

    return ClientError({"Error": {"Code": code, "Message": "boom"}}, op)


class FileRepositoryOutcomeTest(TestCase):

    async def test_missing_object_is_404_not_a_server_error(self):
        with mock.patch.object(file_repository, "_get_s3_client") as client:
            client.return_value.head_bucket.return_value = {}
            client.return_value.get_object.side_effect = _client_error("NoSuchKey")
            with self.assertRaises(Exception) as ctx:
                await file_repository.get_s3_file_stream(
                    StorageContainer.Artifacts, "reports/ghost.png")
        from ninja.errors import HttpError

        self.assertIsInstance(ctx.exception, HttpError)
        self.assertEqual(ctx.exception.status_code, 404)
        # the missing response is never subscripted
        client.return_value.get_object.assert_called_once()

    async def test_access_failure_stays_a_server_error(self):
        with mock.patch.object(file_repository, "_get_s3_client") as client:
            client.return_value.head_bucket.return_value = {}
            client.return_value.get_object.side_effect = _client_error("AccessDenied")
            with self.assertRaises(Exception) as ctx:
                await file_repository.get_s3_file_stream(
                    StorageContainer.Artifacts, "reports/x.png")

        from ninja.errors import HttpError

        self.assertIsInstance(ctx.exception, HttpError)
        self.assertEqual(ctx.exception.status_code, 500)

    def test_get_object_returns_none_only_for_missing_keys(self):
        with mock.patch.object(file_repository, "_get_s3_client") as client:
            client.return_value.get_object.side_effect = _client_error("NoSuchKey")
            self.assertIsNone(file_repository.get_object("a", "k"))
        with mock.patch.object(file_repository, "_get_s3_client") as client:
            client.return_value.get_object.side_effect = _client_error("404")
            self.assertIsNone(file_repository.get_object("a", "k"))
        with mock.patch.object(file_repository, "_get_s3_client") as client:
            client.return_value.get_object.side_effect = _client_error("AccessDenied")
            with self.assertRaises(Exception):
                file_repository.get_object("a", "k")

    def test_delete_object_treats_already_missing_as_deleted(self):
        with mock.patch.object(file_repository, "_get_s3_client") as client:
            client.return_value.delete_object.return_value = {}
            self.assertTrue(file_repository.delete_object("a", "k"))
        with mock.patch.object(file_repository, "_get_s3_client") as client:
            client.return_value.delete_object.side_effect = _client_error("NoSuchKey")
            self.assertTrue(file_repository.delete_object("a", "k"))
        with mock.patch.object(file_repository, "_get_s3_client") as client:
            client.return_value.delete_object.side_effect = _client_error("AccessDenied")
            self.assertFalse(file_repository.delete_object("a", "k"))


class RequestTooBigTest(TestCase):
    """The backend answers oversized bodies with 413 via the explicit handler."""

    def test_request_data_too_big_returns_413(self):
        from ninja import NinjaAPI
        from ninja.testing import TestClient

        from aisc_backend.utils.exception_handlers import register_exception_handlers

        api = NinjaAPI()

        @api.get("/boom")
        def boom(request):
            raise RequestDataTooBig(2_621_440)

        register_exception_handlers(api)
        response = TestClient(api).get("/boom")
        self.assertEqual(response.status_code, 413)
        body = response.json()
        self.assertIn("Request body too large", body.get("detail", ""))
