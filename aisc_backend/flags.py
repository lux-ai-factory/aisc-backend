"""The one feature-flag registry for optional behaviour.

Named flags are read from Django settings (which read the environment) and
default ON — the full profile. Turning the env var off gives the lean profile in
which only the core application — projects, components and evaluations — runs.
Optional routers consult a flag at request time through :func:`require_flag`;
nothing is removed from INSTALLED_APPS, MIDDLEWARE or the root URLconf, so the
same build can switch a feature off without changing its shape.

The optional features and the routers they gate:

    qualification -> routers.qualification   (/projects/{pid}/qualification, ...)
    objectives    -> routers.control_objectives (/projects/{pid}/control-objectives/*)
    controls      -> routers.control         (/projects/{pid}/controls/*)
    reporting     -> routers.report          (/projects/{pid}/reports/*)
"""
from __future__ import annotations

from typing import Callable

from django.conf import settings
from ninja.errors import HttpError

#: flag name -> the Django setting (env var) that carries it.
FLAG_SETTINGS: dict[str, str] = {
    "qualification": "FEATURE_QUALIFICATION",
    "objectives": "FEATURE_OBJECTIVES",
    "controls": "FEATURE_CONTROLS",
    "reporting": "FEATURE_REPORTING",
}


def is_enabled(name: str) -> bool:
    """Whether the named optional feature is switched on. Default ON (full profile)."""
    try:
        setting_name = FLAG_SETTINGS[name]
    except KeyError:
        raise KeyError(
            f"Unknown feature flag {name!r}; known flags: {sorted(FLAG_SETTINGS)}"
        )
    return bool(getattr(settings, setting_name, True))


def require_flag(name: str) -> Callable:
    """A django-ninja dependency for the named feature.

    Enabled: returns ``None`` so the *next* auth callback runs — this is meant to
    be the first entry in a router's ``auth`` list, ahead of ``KeycloakAuth()``.
    Disabled: raises 404, so the optional routes simply do not exist.

    django-ninja runs the entries of a router's ``auth`` list per request, which
    is the runtime hook available for gating a whole router without touching the
    root URLconf.
    """
    def _guard(request):
        if not is_enabled(name):
            raise HttpError(404, f"Feature {name!r} is not enabled")
        return None

    return _guard
