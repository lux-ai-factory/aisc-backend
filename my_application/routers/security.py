from django.http import HttpResponse
from django.views.decorators.csrf import ensure_csrf_cookie, csrf_exempt
from ninja import Router
from ninja.security import django_auth
from ninja_jwt.authentication import JWTAuth

router = Router(tags=["security"])


@router.get("/csrf")
@ensure_csrf_cookie
@csrf_exempt
def get_csrf_token(request):
    return HttpResponse()

@router.get("/test_session_auth", auth=django_auth)
def test_session_auth(request):
    user = request.user
    return user

@router.get("/test_jwt_auth", auth=JWTAuth())
def test_jwt_auth(request):
    user = request.user
    return user