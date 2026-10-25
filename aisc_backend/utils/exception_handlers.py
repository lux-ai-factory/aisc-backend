from ninja import NinjaAPI
import traceback

from django.core.exceptions import ObjectDoesNotExist, MultipleObjectsReturned

from aisc_backend.utils.log_util import get_logger
from config.settings import APP_NAME, LOG_LEVEL

logger = get_logger(APP_NAME, LOG_LEVEL)

INTERNAL_SERVER_ERROR = "Internal server error"

def _message_and_detail(text: str) -> dict[str, str]:
    """One error text under both keys: "detail" as HttpError and validation errors answer, "message" as before."""
    return {"message": text, "detail": text}


def register_exception_handlers(api: NinjaAPI):
    @api.exception_handler(ObjectDoesNotExist)
    def not_found_handler(request, exc):
        return api.create_response(
            request,
            _message_and_detail(f"{exc} — Endpoint: {request.method} {request.path}"),
            status=404,
        )

    @api.exception_handler(MultipleObjectsReturned)
    def conflict_handler(request, exc):
        return api.create_response(
            request,
            _message_and_detail(f"{exc} — Endpoint: {request.method} {request.path}"),
            status=409,
        )

    @api.exception_handler(Exception)
    def global_exception_handler(request, exc: Exception):
        tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        logger.error(
            f"Unhandled exception while processing request "
            f"{request.method} {request.path}\n{tb}"
        )
        # the exception's text and traceback go to the log only, never to the caller
        return api.create_response(request, _message_and_detail(INTERNAL_SERVER_ERROR), status=500)
