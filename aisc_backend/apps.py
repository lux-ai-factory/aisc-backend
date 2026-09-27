import sys
import warnings

from django.apps import AppConfig

# Commands that need no database of a mode: the test runner makes its own, the other two read none.
# migrate is checked: a migrate of the other mode would remake (or drop) the login tables.
_NOT_CHECKED = ("test", "makemigrations", "collectstatic")
# Django's options that take their value as the next argument.
_OPTIONS_WITH_A_VALUE = ("--settings", "--pythonpath", "--verbosity", "-v")


def command_of(argv: list[str]) -> str | None:
    """The command of a manage.py argv: the first argument after argv[0] that is not an option
    (or the value of one of Django's options given as a separate argument)."""
    args = iter(argv[1:])
    for arg in args:
        if arg in _OPTIONS_WITH_A_VALUE:
            next(args, None)
        elif not arg.startswith("-"):
            return arg
    return None


class AiscBackendConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'aisc_backend'
    verbose_name = 'AISC Backend'

    def ready(self):
        from aisc_backend import deployment

        if deployment.is_configurator():
            from aisc_backend.signals import system_stamp  # noqa: F401  (the test stamp, WP9)
        from django.db.models.signals import pre_migrate
        pre_migrate.connect(deployment.before_migrate, dispatch_uid="aisc_deployment_before_migrate")
        self._check_the_database_mode()

    @staticmethod
    def _check_the_database_mode():
        """Refuse a database made by the other mode. Project databases (configurator on Postgres)
        are only ever made by a configurator engine: `default` is the dummy backend there, and
        a standalone engine pointed at one sees its marker through its own `default`.
        So in the deployed configurator shape (project databases) nothing is checked at start:
        only the standalone side refuses (migrate is also refused by deployment.before_migrate)."""
        from django.conf import settings
        from django.db import DatabaseError, connection

        from aisc_backend import deployment

        if command_of(sys.argv) in _NOT_CHECKED:
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
