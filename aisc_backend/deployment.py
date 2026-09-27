"""Where this engine runs: on its own, or inside the Sandbox Configurator.

One switch, AISC_DEPLOYMENT, read once. Every behaviour that differs between the two asks this
module (docs/superpowers/specs/2026-09-27-engine-deployment-modes.md has the list); nothing else
reads the variable.
"""
import os
from collections.abc import Mapping

from django.core.exceptions import ImproperlyConfigured

STANDALONE = "standalone"
CONFIGURATOR = "configurator"
MODES = (STANDALONE, CONFIGURATOR)


def mode(env: Mapping[str, str] = os.environ) -> str:
    if "AISC_DEPLOYMENT" not in env:
        return STANDALONE
    value = env["AISC_DEPLOYMENT"].strip().lower()
    if value not in MODES:
        raise ImproperlyConfigured(
            f"AISC_DEPLOYMENT must be {STANDALONE} or {CONFIGURATOR}, not {env['AISC_DEPLOYMENT']!r}")
    return value


def _postgres(env: Mapping[str, str]) -> bool:
    return "postgresql" in env.get("DB_ENGINE", "django.db.backends.sqlite3")


def project_databases(env: Mapping[str, str] = os.environ) -> bool:
    """One database per project: the Configurator, on Postgres. Standalone is always one database."""
    return mode(env) == CONFIGURATOR and _postgres(env)


def check_environment(env: Mapping[str, str] = os.environ, testing: bool | None = None) -> None:
    """The Configurator makes a database per project, which takes Postgres. The test runner alone may
    run it on one sqlite database (the configurator unit tests do, as they did before the modes)."""
    if testing is None:
        import sys
        testing = sys.argv[1:2] == ["test"]
    if mode(env) == CONFIGURATOR and not _postgres(env) and not testing:
        raise ImproperlyConfigured(
            f"AISC_DEPLOYMENT is {CONFIGURATOR}: DB_ENGINE must be django.db.backends.postgresql "
            "(one database per project)")


def is_configurator() -> bool:
    from django.conf import settings
    return settings.AISC_DEPLOYMENT == CONFIGURATOR


def is_standalone() -> bool:
    return not is_configurator()
