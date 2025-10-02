from ninja import Router
from ninja.security import django_auth
from ninja_jwt.authentication import JWTAuth

router = Router(tags=["accounts"], auth=[JWTAuth(), django_auth])

@router.get("/me")
def me(request):
    user = request.user
    return user