from django.contrib.auth.models import User
from ninja import Router, ModelSchema
from ninja.security import django_auth
from ninja_jwt.authentication import JWTAuth

router = Router(tags=["accounts"])

class UserOutSchema(ModelSchema):
    class Meta:
        model = User
        fields = ["username", "is_staff", "is_active", "is_superuser"]

@router.get("/me", response=UserOutSchema, auth=[JWTAuth(), django_auth])
def me(request):
    user = request.user
    return user

@router.get("/me_session_auth", response=UserOutSchema, auth=django_auth)
def me_session_auth(request):
    user = request.user
    return user

@router.get("/me_jwt_auth", response=UserOutSchema, auth=JWTAuth())
def me_jwt_auth(request):
    user = request.user
    return user