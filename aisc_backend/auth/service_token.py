"""
Service-token door for backend<->isolated-app calls.

An isolated app (qualification, report-composer, ...) calls the backend, or the
backend calls one, over HTTP with the CALLER's Bearer token forwarded; but
service-to-service calls that have no user in front carry a shared secret in
`X-AISC-Service-Token` instead. This door validates that header against the
expected secrets.

Two sides live here:
  - ServiceTokenAuth  (django-ninja) — guards backend endpoints an app calls.
  - service_token_headers()         — builds the header the backend sends when
    it calls an app.
"""
from ninja.security import APIKeyHeader

from config.settings import SERVICE_TOKEN, SERVICE_TOKEN_FOR_APPS


class ServiceTokenAuth(APIKeyHeader):
    """Accept `X-AISC-Service-Token` when it matches the configured secret.

    Same deny-by-default style as the other doors: no header, or a wrong one,
    is refused. Used on endpoints meant only for the trusted apps (and their
    tokens are made by scripts/secrets.sh, never committed).
    """

    param_name = "X-AISC-Service-Token"

    def authenticate(self, request, key):
        if SERVICE_TOKEN and key == SERVICE_TOKEN:
            return "service"
        return None


def service_token_headers() -> dict[str, str]:
    """The header to send when the backend calls an isolated app's service door."""
    if not SERVICE_TOKEN_FOR_APPS:
        return {}
    return {"X-AISC-Service-Token": SERVICE_TOKEN_FOR_APPS}
