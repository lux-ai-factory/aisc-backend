"""Authentication classes for Django Ninja routes."""

from typing import Any, Optional

from django.conf import settings
from django.http import HttpRequest
from ninja.security.apikey import APIKeyCookie


class SessionAuth(APIKeyCookie):
    """Require an authenticated session."""

    param_name: str = settings.SESSION_COOKIE_NAME

    def authenticate(self, request: HttpRequest, key: Optional[str]) -> Optional[Any]:
        if request.user.is_authenticated:
            return request.user
        return None


class AdminAuth(APIKeyCookie):
    """Require an authenticated session with the 'admin' Django Group."""

    param_name: str = settings.SESSION_COOKIE_NAME

    def authenticate(self, request: HttpRequest, key: Optional[str]) -> Optional[Any]:
        if request.user.is_authenticated and request.user.groups.filter(name="admin").exists():
            return request.user
        return None


require_auth = SessionAuth()
require_admin = AdminAuth()
