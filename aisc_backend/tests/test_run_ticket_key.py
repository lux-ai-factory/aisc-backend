"""The run ticket's key is the backend's alone (security review 2026-10-06), and two first visits to a
project make one row (code review 2026-10-06).

I7.3's ticket is an HMAC over `<platform pid>.<evaluation pid>`. It was keyed with SECRET_KEY, which the
eval worker also holds (it decrypts plugin settings with it) and the worker runs third-party plugin code:
a plugin could mint a ticket for any project and evaluation, and with the internal key call the internal
routes for them. RUN_TICKET_KEY is given to the backend only; the configurator refuses to start without it,
or with it equal to DJANGO_SECRET_KEY. Standalone has one project and keeps SECRET_KEY when it is unset.

POST /projects/for-platform/{pid} looked the row up, then made it: two first visits at once (the home page
and the install dialog both call it) made the second fail on one_project_per_platform_project with a 500.
"""
import asyncio
import hashlib
import hmac
import unittest.mock as mock

from django.core.exceptions import ImproperlyConfigured
from django.db import IntegrityError
from django.test import SimpleTestCase, override_settings

from aisc_backend import deployment, projectdb

PROJECT = "00000000-0000-0000-0000-00000000000a"
EVALUATION = "00000000-0000-0000-0000-00000000000b"
POSTGRES = "django.db.backends.postgresql"


NOW = 1_800_000_000
DAY = 24 * 60 * 60


def signed(key, expires=NOW + DAY):
    """A ticket as run_ticket makes it: `<expiry>.<hex HMAC over pid.evaluation.expiry>`."""
    mac = hmac.new(key.encode(), f"{PROJECT}.{EVALUATION}.{expires}".encode(), hashlib.sha256).hexdigest()
    return f"{expires}.{mac}"


def at(when):
    return mock.patch.object(projectdb, "_now", return_value=when)


class TheTicketsKey(SimpleTestCase):
    @override_settings(SECRET_KEY="the-django-secret", RUN_TICKET_KEY="the-ticket-key")
    def test_a_ticket_is_signed_with_the_run_ticket_key(self):
        with at(NOW):
            self.assertEqual(projectdb.run_ticket(PROJECT, EVALUATION), signed("the-ticket-key"))
            self.assertTrue(projectdb.ticket_is_valid(PROJECT, EVALUATION, signed("the-ticket-key")))

    @override_settings(SECRET_KEY="the-django-secret", RUN_TICKET_KEY="the-ticket-key")
    def test_one_signed_with_the_secret_key_the_worker_holds_is_refused(self):
        with at(NOW):
            self.assertFalse(projectdb.ticket_is_valid(PROJECT, EVALUATION, signed("the-django-secret")))


@override_settings(SECRET_KEY="the-django-secret", RUN_TICKET_KEY="the-ticket-key")
class TheTicketExpires(SimpleTestCase):
    """A ticket lasts RUN_TICKET_TTL_SECONDS (a day by default, longer than any run), then is refused: one
    seen in a log or kept by a plugin opens nothing for ever (code review 2026-10-06)."""

    def test_it_lasts_a_day_by_default(self):
        with at(NOW):
            ticket = projectdb.run_ticket(PROJECT, EVALUATION)
        self.assertEqual(ticket.split(".", 1)[0], str(NOW + DAY))
        with at(NOW + DAY - 1):
            self.assertTrue(projectdb.ticket_is_valid(PROJECT, EVALUATION, ticket))

    def test_an_expired_ticket_is_refused(self):
        with at(NOW):
            ticket = projectdb.run_ticket(PROJECT, EVALUATION)
        with at(NOW + DAY + 1):
            self.assertFalse(projectdb.ticket_is_valid(PROJECT, EVALUATION, ticket))

    def test_a_ticket_whose_expiry_was_changed_is_refused(self):
        with at(NOW):
            mac = projectdb.run_ticket(PROJECT, EVALUATION).split(".", 1)[1]
        with at(NOW + DAY + 1):
            self.assertFalse(projectdb.ticket_is_valid(PROJECT, EVALUATION, f"{NOW + 10 * DAY}.{mac}"))

    def test_an_old_ticket_without_expiry_is_refused(self):
        old = hmac.new(b"the-ticket-key", f"{PROJECT}.{EVALUATION}".encode(), hashlib.sha256).hexdigest()
        with at(NOW):
            self.assertFalse(projectdb.ticket_is_valid(PROJECT, EVALUATION, old))
            self.assertFalse(projectdb.ticket_is_valid(PROJECT, EVALUATION, "x.y.z"))

    @override_settings(RUN_TICKET_TTL_SECONDS=60)
    def test_its_life_is_a_setting(self):
        with at(NOW):
            self.assertEqual(projectdb.run_ticket(PROJECT, EVALUATION).split(".", 1)[0], str(NOW + 60))

    @override_settings(AISC_DEPLOYMENT=deployment.CONFIGURATOR, RUN_TICKET_KEY="")
    def test_the_configurator_never_falls_back_to_the_secret_key(self):
        with self.assertRaisesRegex(ImproperlyConfigured, "RUN_TICKET_KEY"):
            projectdb.run_ticket(PROJECT, EVALUATION)

    @override_settings(AISC_DEPLOYMENT=deployment.STANDALONE, SECRET_KEY="the-django-secret", RUN_TICKET_KEY="")
    def test_standalone_keeps_the_secret_key_when_it_is_unset(self):
        with at(NOW):
            self.assertEqual(projectdb.run_ticket(PROJECT, EVALUATION), signed("the-django-secret"))


class TheConfiguratorRefusesToStart(SimpleTestCase):
    def env(self, **more):
        return {"AISC_DEPLOYMENT": "configurator", "DB_ENGINE": POSTGRES, "DJANGO_SECRET_KEY": "s",
                "AUTH_ENABLED": "true", **more}

    def test_without_a_run_ticket_key(self):
        with self.assertRaisesRegex(ImproperlyConfigured, "RUN_TICKET_KEY"):
            deployment.check_environment(self.env(), testing=False)

    def test_with_the_secret_key_as_run_ticket_key(self):
        with self.assertRaisesRegex(ImproperlyConfigured, "RUN_TICKET_KEY"):
            deployment.check_environment(self.env(RUN_TICKET_KEY="s"), testing=False)

    def test_with_a_key_of_its_own(self):
        deployment.check_environment(self.env(RUN_TICKET_KEY="t"), testing=False)

    def test_standalone_needs_none(self):
        deployment.check_environment({"DB_ENGINE": POSTGRES}, testing=False)

    def test_the_settings_source_carries_both_keys(self):
        read = {"RUN_TICKET_KEY": "t", "DJANGO_SECRET_KEY": "s"}.get
        self.assertEqual(deployment.settings_source(read), {"RUN_TICKET_KEY": "t", "DJANGO_SECRET_KEY": "s"})


class TwoFirstVisitsAtOnce(SimpleTestCase):
    def test_the_second_gets_the_row_the_first_made(self):
        from aisc_backend.routers import platform_project as route

        made_by_the_other = object()
        filter_calls = []

        async def filter(**kwargs):
            filter_calls.append(kwargs)
            return [] if len(filter_calls) == 1 else [made_by_the_other]

        async def create(*args, **kwargs):
            raise IntegrityError('duplicate key value violates unique constraint "one_project_per_platform_project"')

        with mock.patch.object(route.membership, "require"), \
                mock.patch.object(route, "platform_project_name", return_value="P"), \
                mock.patch.object(route.project_repository, "filter", side_effect=filter), \
                mock.patch.object(route.project_repository, "create", side_effect=create), \
                mock.patch.object(route, "log_action") as logged:
            answer = asyncio.run(route.project_for_platform(mock.Mock(), PROJECT))
        self.assertIs(answer, made_by_the_other)
        logged.assert_not_called()
