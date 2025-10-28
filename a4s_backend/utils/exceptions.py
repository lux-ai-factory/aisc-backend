from ninja import NinjaAPI

from django.core.exceptions import ObjectDoesNotExist, MultipleObjectsReturned

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
