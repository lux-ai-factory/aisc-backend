import os
from ninja.security import APIKeyHeader

class InternalSharedKeyAuth(APIKeyHeader):
    param_name = "X-Internal-Secret"

    def authenticate(self, request, key):
        expected_secret = os.environ.get("INTERNAL_API_KEY")

        if expected_secret and key == expected_secret:
            return "internal-service"
        return None
