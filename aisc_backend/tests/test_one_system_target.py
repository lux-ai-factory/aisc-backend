"""A system has one system target (code review 2026-10-06).

The evaluation form's GET made sure of the system target by checking, then creating, with no lock and no
constraint: two forms loaded at once could make it twice, and the form then offered two "Target · System".
Migration 0022 merges duplicates (what referred to one refers to the one kept) and adds a unique index on
(system, the component's value) for system targets; the create is safe against a concurrent one.
"""
import importlib
import unittest.mock as mock
import uuid

from asgiref.sync import async_to_sync
from django.apps import apps as django_apps
from django.db import connection
from django.test import TransactionTestCase

from aisc_backend.models import AIComponent, AISystem, Project
from aisc_backend.routers import target_input

MIGRATION = importlib.import_module("aisc_backend.migrations.0022_one_system_target_per_system")


class OneSystemTarget(TransactionTestCase):
    def setUp(self):
        self.project = Project.objects.create(name="MCAS", description="", platform_project_id=uuid.uuid4())
        self.system = AISystem.objects.create(name="MCAS", description="", project=self.project)
        self.value = target_input.system_reference(self.project.platform_project_id)

    def targets(self):
        return AIComponent.objects.filter(system=self.system, json_value__value=self.value)

    def test_two_creates_at_once_make_one(self):
        # both requests saw no target before either created it
        with mock.patch.object(target_input, "_system_target_exists", return_value=False):
            async_to_sync(target_input.ensure_system_target)()
            async_to_sync(target_input.ensure_system_target)()
        self.assertEqual(self.targets().count(), 1)

    def test_the_database_refuses_a_second(self):
        from django.db import IntegrityError, transaction
        AIComponent.objects.create(system=self.system, name="T", component_type="resource",
                                   json_value={"value": self.value})
        with self.assertRaises(IntegrityError), transaction.atomic():
            AIComponent.objects.create(system=self.system, name="T2", component_type="resource",
                                       json_value={"value": self.value})

    def test_the_migration_merges_duplicates_and_keeps_what_referred_to_them(self):
        with connection.schema_editor() as editor:
            MIGRATION.drop_index(django_apps, editor)
        keep = AIComponent.objects.create(system=self.system, name="T1", component_type="resource",
                                          json_value={"value": self.value})
        extra = AIComponent.objects.create(system=self.system, name="T2", component_type="resource",
                                           json_value={"value": self.value})
        derived = AIComponent.objects.create(system=self.system, name="D", component_type="datashape",
                                             json_value={}, source_dataset=extra)
        with connection.schema_editor() as editor:
            MIGRATION.merge_duplicates(django_apps, editor)
            MIGRATION.create_index(django_apps, editor)
        self.assertEqual(list(self.targets().values_list("pk", flat=True)), [keep.pk])
        derived.refresh_from_db()
        self.assertEqual(derived.source_dataset_id, keep.pk)
