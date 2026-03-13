"""
Extend allauth SessionTokenStrategy to return JWT generated using Ninja JWT

Code is only executed when using the allauth 'app' client endpoints
'browser' client endpoints rely on django session cookies for auth
"""

from dataclasses import dataclass
from typing import Optional, Dict, Any
from allauth.headless.tokens.sessions import SessionTokenStrategy
from ninja_jwt.tokens import RefreshToken
from django.http import HttpRequest


@dataclass
class TokenPairOut:
    refresh_token: str
    access_token: str


class SessionAndJWTStrategy(SessionTokenStrategy):
    def create_access_token_payload(self, request: HttpRequest) -> Optional[Dict[str, Any]]:
        user = request.user
        if not user or not user.is_authenticated:
            return None

        refresh_token = RefreshToken.for_user(user)
        return TokenPairOut(refresh_token=str(refresh_token), access_token=str(refresh_token.access_token)).__dict__