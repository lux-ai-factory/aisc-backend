from ninja import NinjaAPI
import traceback

from django.core.exceptions import ObjectDoesNotExist, MultipleObjectsReturned
from django.db import IntegrityError

from aisc_backend.utils.log_util import get_logger
from config.settings import APP_NAME, LOG_LEVEL

logger = get_logger(APP_NAME, LOG_LEVEL)

def register_exception_handlers(api: NinjaAPI):
    @api.exception_handler(ObjectDoesNotExist)
    def not_found_handler(request, exc):
        return api.create_response(
            request,
            {"message": f"{exc} — Endpoint: {request.method} {request.path}"},
            status=404,
        )

    @api.exception_handler(MultipleObjectsReturned)
    def conflict_handler(request, exc):
        return api.create_response(
            request,
            {"message": f"{exc} — Endpoint: {request.method} {request.path}"},
            status=409,
        )

    @api.exception_handler(IntegrityError)
    def integrity_error_handler(request, exc: IntegrityError):
        # A unique/foreign-key constraint rejected the write. Report it as a
        # conflict rather than a 500 so callers can react (e.g. duplicate project
        # name). The detail stays in the log; the response carries the reason.
        logger.warning(
            f"IntegrityError while processing request "
            f"{request.method} {request.path}: {exc}"
        )
        return api.create_response(
            request,
            {"message": str(exc), "detail": "constraint violation"},
            status=409,
        )

    @api.exception_handler(Exception)
    def global_exception_handler(request, exc: Exception):
        tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        logger.error(
            f"Unhandled exception while processing request "
            f"{request.method} {request.path}\n{tb}"
        )
        return api.create_response(
            request,
            {"message": f"{exc} — Endpoint: {request.method} {request.path}"},
            status=500,
        )
