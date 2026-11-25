from ninja import NinjaAPI
from aisc_backend.services.qualification_service import QualificationServiceError
from aisc_backend.services.report_service import ReportServiceError
import traceback

from django.core.exceptions import (
    ObjectDoesNotExist,
    MultipleObjectsReturned,
    RequestDataTooBig,
)

from aisc_backend.utils.log_util import get_logger
from config.settings import APP_NAME, LOG_LEVEL

logger = get_logger(APP_NAME, LOG_LEVEL)

def register_exception_handlers(api: NinjaAPI):
    @api.exception_handler(QualificationServiceError)
    @api.exception_handler(ReportServiceError)
    def upstream_service_error(request, exc):
        return api.create_response(request, {"detail": str(exc)}, status=502)

    @api.exception_handler(RequestDataTooBig)
    def too_large_handler(request, exc):
        # A report request that stops accepting image bytes must not let an
        # oversized body fall through to the generic 500 handler.
        limit = exc.args[0] if exc.args else ""
        return api.create_response(
            request,
            {"detail": f"Request body too large{': ' + str(limit) if limit else ''}. "
                       "Upload charts individually, then submit the report."},
            status=413,
        )

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
