import sys
import warnings

from django.apps import AppConfig

# Commands that make or migrate a database themselves, so there is no mode to check yet.
_MAKES_ITS_OWN_DATABASE = ("migrate", "migrate_projects", "makemigrations", "test", "collectstatic")


class AiscBackendConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'aisc_backend'
    verbose_name = 'AISC Backend'

    def ready(self):
        from aisc_backend import deployment

        if deployment.is_configurator():
            from aisc_backend.signals import system_stamp  # noqa: F401  (the test stamp, WP9)
        self._check_the_database_mode()

    @staticmethod
    def _check_the_database_mode():
        """Refuse a database made by the other mode. Project databases (configurator on Postgres)
        are only ever made by a configurator engine: `default` is the dummy backend there, and
        a standalone engine pointed at one sees its marker through its own `default`."""
        from django.conf import settings
        from django.db import DatabaseError, connection

        from aisc_backend import deployment

        if sys.argv[1:2] and sys.argv[1] in _MAKES_ITS_OWN_DATABASE:
            return
        if settings.PROJECT_DATABASES or connection.vendor == "dummy":
            return
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", RuntimeWarning)  # a query during app start, on purpose
                deployment.assert_database_mode(connection)
        except DatabaseError:
            return  # not reachable yet (starting before Postgres): the first query will say so
        finally:
            connection.close()
