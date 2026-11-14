import hmac
import os
from ninja.security import APIKeyHeader

class InternalSharedKeyAuth(APIKeyHeader):
    param_name = "X-Internal-Secret"

    def authenticate(self, request, key):
        expected_secret = os.environ.get("INTERNAL_API_KEY")

        if not expected_secret or key is None:
            return None
        # Constant time, so the time an answer takes says nothing about how much of a guess was right.
        if hmac.compare_digest(key.encode(), expected_secret.encode()):
            return "internal-service"
        return None
