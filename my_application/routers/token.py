from jwt import InvalidTokenError
from ninja import Router, Schema
from ninja_jwt.tokens import RefreshToken

router = Router(tags=["tokens"])

class TokenPairOut(Schema):
    refresh_token: str
    access_token: str

class RefreshTokenIn(Schema):
    refresh_token: str

@router.post("/refresh", response=TokenPairOut)
def refresh(request, data: RefreshTokenIn):
    try:
        refresh_token = RefreshToken(data.refresh_token)
        return TokenPairOut(refresh_token=str(refresh_token), access_token=str(refresh_token.access_token))
    except InvalidTokenError:
        return 401, {"detail": "Invalid refresh token"}
