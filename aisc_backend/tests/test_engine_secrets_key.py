"""The engine encrypts stored secrets with a key of its own, ENGINE_SECRETS_KEY, not one derived from
Django's SECRET_KEY, so rotating Django's key no longer makes every stored value unreadable. Values stored
under the Django key before still decrypt. The Configurator refuses to start without the key, or with it
equal to DJANGO_SECRET_KEY.
"""
from cryptography.fernet import Fernet, InvalidToken
from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase, override_settings

from aisc_backend import deployment
from aisc_backend.utils.encryption import decrypt_value, derive_key, encrypt_value

NEW = Fernet.generate_key().decode()
CONFIGURATOR = {"AISC_DEPLOYMENT": "configurator", "DB_ENGINE": "django.db.backends.postgresql",
                "DJANGO_SECRET_KEY": "django-key-for-the-test", "RUN_TICKET_KEY": "t", "AUTH_ENABLED": "true"}


class TheEngineKeepsItsOwnKey(SimpleTestCase):

    @override_settings(ENGINE_SECRETS_KEY=NEW, SECRET_KEY="django-key-1")
    def test_a_new_value_is_encrypted_with_the_engine_key(self):
        token = encrypt_value("value-for-the-test")
        try:
            plain = Fernet(NEW.encode()).decrypt(token.encode())
        except InvalidToken:
            self.fail("a new value is not encrypted with ENGINE_SECRETS_KEY")
        self.assertEqual(plain, b"value-for-the-test")
        with self.assertRaises(InvalidToken):
            Fernet(derive_key("django-key-1")).decrypt(token.encode())

    def test_a_value_stored_under_the_django_key_still_decrypts(self):
        legacy = Fernet(derive_key("django-key-1")).encrypt(b"old").decode()
        with override_settings(ENGINE_SECRETS_KEY=NEW, SECRET_KEY="django-key-1"):
            self.assertEqual(decrypt_value(legacy), "old")

    def test_a_new_django_key_leaves_new_values_readable(self):
        with override_settings(ENGINE_SECRETS_KEY=NEW, SECRET_KEY="django-key-1"):
            token = encrypt_value("v")
        with override_settings(ENGINE_SECRETS_KEY=NEW, SECRET_KEY="django-key-2"):
            try:
                self.assertEqual(decrypt_value(token), "v")
            except InvalidToken:
                self.fail("a new Django key made a value stored with the engine key unreadable")


class TheConfiguratorNeedsTheEngineKey(SimpleTestCase):

    def test_configurator_refuses_to_start_without_the_engine_secrets_key(self):
        with self.assertRaisesRegex(ImproperlyConfigured, "ENGINE_SECRETS_KEY"):
            deployment.check_environment(dict(CONFIGURATOR), testing=False)

    def test_configurator_refuses_an_engine_key_equal_to_the_django_key(self):
        env = {**CONFIGURATOR, "ENGINE_SECRETS_KEY": CONFIGURATOR["DJANGO_SECRET_KEY"]}
        with self.assertRaisesRegex(ImproperlyConfigured, "ENGINE_SECRETS_KEY"):
            deployment.check_environment(env, testing=False)

    def test_configurator_starts_with_an_engine_key_of_its_own(self):
        deployment.check_environment({**CONFIGURATOR, "ENGINE_SECRETS_KEY": NEW}, testing=False)

    def test_a_process_without_the_django_key_needs_no_engine_key(self):
        # the ledger sender and migrate_projects store and read no credentials: they get neither key
        env = {k: v for k, v in CONFIGURATOR.items() if k != "DJANGO_SECRET_KEY"}
        deployment.check_environment(env, testing=False)

    def test_the_settings_source_carries_the_engine_key(self):
        self.assertIn("ENGINE_SECRETS_KEY", deployment.settings_source({"ENGINE_SECRETS_KEY": NEW}.get))
