"""
One assessed-system identity per project (VER-01, slice 1).

The core `AISystem` is a project's assessed target; the qualification `System`
(the AI card's subject) is optional content about it. Qualification content must
link to the core system rather than name one of its own, so a project can never
resolve to two different assessed systems.
"""
from django.test import TestCase

from aisc_backend.models import AISystem
from aisc_backend.models.project import Project
from aisc_backend.models.system import System
from aisc_backend.repositories.ai_component_repository import AISystemRepository
from aisc_backend.repositories.system_repository import SystemRepository


class AssessedSystemIdentityTest(TestCase):

    async def _project(self):
        return await Project.objects.acreate(
            name="assessed-system", description="", status="Ready"
        )

    async def test_core_and_qualification_resolve_to_one_assessed_system(self):
        project = await self._project()

        # Core and qualification content, each created through its own path.
        core = await AISystemRepository().get_or_create_for_project(project)
        qualification = await SystemRepository().get_or_create(project)

        # The project has exactly one assessed-system identity, and the
        # qualification content resolves to it — not to a system of its own.
        self.assertEqual(await AISystem.objects.filter(project=project).acount(), 1)
        self.assertEqual(qualification.aisystem_id, core.pk)
        self.assertEqual(
            await System.objects.filter(project=project, aisystem=core).acount(), 1
        )

        # Content written before the link existed is linked on resolution too,
        # and still resolves to that same one identity.
        other = await self._project()
        orphan = await System.objects.acreate(
            project=other, name="legacy card subject", description=""
        )
        self.assertIsNone(orphan.aisystem_id)

        resolved = await SystemRepository().get_or_create(other)

        linked_core = await AISystem.objects.filter(project=other).afirst()
        self.assertIsNotNone(linked_core)
        self.assertEqual(resolved.aisystem_id, linked_core.pk)
        self.assertEqual(await AISystem.objects.filter(project=other).acount(), 1)
