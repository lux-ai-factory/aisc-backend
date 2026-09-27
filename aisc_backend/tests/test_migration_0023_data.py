"""S1.2 (pipeline-2026-09-23, WP1): 0023 puts every part back on its project's one AISystem.

Migrates back to 0022 (where parts belong to a project), writes parts through the
historical models, migrates forward to 0023 and checks that every part keeps its
pid and points at the one ai_system of its project, and that that system is named
as get_or_create_aisystem names it (f"{project.name} system", description "").

Going back to 0022 needs 0023 to be reversible on an empty table (RunPython and
RunSQL steps with a reverse, a noop reverse is enough). Runs on sqlite and on
Postgres; on Postgres without core.* in the test DB, as the unit suite has it.
"""
import uuid

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase

APP = "aisc_backend"
BEFORE = (APP, "0022_parts_belong_to_a_version_of_the_one_system")
AFTER = (APP, "0023_one_system_per_project_again")


class Migration0023MovesPartsBackOntoTheSystem(TransactionTestCase):

    def _leaf(self, executor):
        return executor.loader.graph.leaf_nodes(APP)

    def test_s1_2_every_part_keeps_its_pid_and_joins_its_project_s_one_system(self):
        executor = MigrationExecutor(connection)
        if AFTER not in executor.loader.disk_migrations:
            self.fail(f"migration {AFTER[1]} is missing (WP1)")
        leaf = self._leaf(executor)
        try:
            executor.migrate([BEFORE])
            old = MigrationExecutor(connection).loader.project_state([BEFORE]).apps
            Project = old.get_model(APP, "Project")
            AIComponent = old.get_model(APP, "AIComponent")
            p1 = Project.objects.create(name="MCAS", status="ready")
            p2 = Project.objects.create(name="Other", status="ready")
            parts = {
                uuid.uuid4(): p1, uuid.uuid4(): p1, uuid.uuid4(): p2,
            }
            for pid, project in parts.items():
                AIComponent.objects.create(pid=pid, name=f"part {pid}", description="",
                                           data="", component_type="dataset",
                                           project=project, lineage=pid)

            executor = MigrationExecutor(connection)
            executor.migrate([AFTER])
            new = MigrationExecutor(connection).loader.project_state([AFTER]).apps
            AISystem = new.get_model(APP, "AISystem")
            AIComponent = new.get_model(APP, "AIComponent")

            self.assertEqual(AISystem.objects.count(), 2, "one ai_system per engine project")
            for project_id, name in ((p1.id, "MCAS"), (p2.id, "Other")):
                system = AISystem.objects.get(project_id=project_id)
                self.assertEqual(system.name, f"{name} system")
                self.assertEqual(system.description, "")

            self.assertEqual(AIComponent.objects.count(), len(parts))
            for pid, project in parts.items():
                part = AIComponent.objects.get(pid=pid)
                self.assertEqual(part.system.project_id, project.id)
        finally:
            MigrationExecutor(connection).migrate(leaf)
