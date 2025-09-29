from typing import Dict, Type

from django.contrib.auth.models import User
from ninja import Schema
from ninja_jwt.schema import TokenObtainInputSchemaBase
from ninja_jwt.tokens import RefreshToken
from ninja_jwt.exceptions import AuthenticationFailed

class TokenPairOut(Schema):
    refresh: str
    access: str

class VerifiedEmailTokenObtainSchema(TokenObtainInputSchemaBase):
    @classmethod
    def get_response_schema(cls) -> Type[Schema]:
        return TokenPairOut

    @classmethod
    def get_token(cls, user: User) -> Dict:
        # Require at least one verified email (django-allauth)
        if not user.emailaddress_set.filter(verified=True).exists():
            raise AuthenticationFailed("Email not verified.")
        refresh = RefreshToken.for_user(user)
        return {"refresh": str(refresh), "access": str(refresh.access_token)}
