from django.apps import AppConfig


class AiscBackendConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'aisc_backend'
    verbose_name = 'AISC Backend'

    def ready(self):
        from aisc_backend.signals import system_stamp  # noqa: F401  (the test stamp, WP9)
