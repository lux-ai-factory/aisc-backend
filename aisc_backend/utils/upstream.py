"""Shared upstream-service failure type.

Optional domains raise their own exception classes, but the core exception handler
must not import those domains. Subclass this base so the handler can register one
class without a core -> optional import.
"""


class UpstreamServiceError(RuntimeError):
    """An upstream service answered with a failure."""
