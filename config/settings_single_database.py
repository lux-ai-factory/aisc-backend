"""One database for the whole engine: test and harness settings only.

Never used by a deployed engine (isolation 2026-09-25, 01-specs.md I7.1: a
deployed engine keeps every table in the database of its project). This module
exists so that the backend suite written before isolation, and guard-frozen's
`--orders` mode, can migrate and use one database as they always did:

    DJANGO_SETTINGS_MODULE=config.settings_single_database manage.py test ...

A module and not an environment switch: isolation_support.settings_probe forces
config.settings and copies os.environ, so a switch variable would leak into the
probe of a deployed engine.
"""
from config.settings import *  # noqa: F401,F403
from config.settings import _single_database

PROJECT_DATABASES = False
DATABASES = {"default": _single_database()}
DATABASE_ROUTERS = []
PROJECT_DATABASE_TEMPLATE = None
