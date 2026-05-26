from logging import DEBUG

from ninja import NinjaAPI

from aisc_backend.utils.log_util import get_logger
from config.settings import APP_NAME, LOG_LEVEL

logger = get_logger(APP_NAME, LOG_LEVEL)


class LoggingNinjaAPI(NinjaAPI):
    def create_response(self, request, data, *, status=200, **kwargs):

        client_ip, client_port = request.scope.get("client") or (None, None)
        method = request.method
        scheme = request.scheme
        proto = request.scope.get("http_version", "1.1")
        user_agent = request.headers.get("user-agent", "").lower()
        query_string = request.META.get("QUERY_STRING") or None

        path = request.path
        if query_string is not None:
            path = path + "?" + query_string

        if "python-requests" in user_agent or "aiohttp" in user_agent:
            client_type = "aisc-eval"
        elif "mozilla" in user_agent or "chrome" in user_agent or "safari" in user_agent:
            client_type = "aisc-web"
        else:
            client_type = "unknown"

        request_string = None

        if logger.get_level() == DEBUG and not request.FILES and request.body != b"":
            request_body = request.body.decode("utf-8")
            request_string = f'\t-\tRequest body: {request_body}'

        response =  super().create_response(request, data, status=status, **kwargs)
        status_code = response.status_code

        response_string = None
        if logger.get_level() == DEBUG and hasattr(response, "content"):
            content = response.content.decode("utf-8")
            response_string = f'\t-\tResponse body: {content}'


        logger.info(
            f'{client_ip}:{client_port} ({client_type}) '
            f'{method} {path} {scheme}/{proto} {status_code}'
            f'{request_string or ""}'
            f'{response_string or ""}'
        )

        return response
