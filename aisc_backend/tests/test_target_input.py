"""Every evaluation names its target (targets plan v2, 2026-09-29, EN1 to EN4).

In configurator mode the plugin input definitions the evaluation form reads end with one more,
required `resource` input, `target`: the assessed system or one of its components, whose engine
components the platform keeps (`target:<platform pid>/<key>`). The frozen plugin route is not
touched: a route of this module is mounted in front of it, only in configurator mode, and reuses
its handler. The form's own required-input check then asks every run for a target, and the frozen
create route stores the pick as an ordinary EvaluationInput. Standalone is unchanged.
"""
import unittest
import unittest.mock as mock
import uuid
from pathlib import Path

from asgiref.sync import async_to_sync
from django.test import SimpleTestCase, override_settings

from aisc_backend.routers import target_input
from aisc_plugin_interface import InputDefinition, InputType

URLS = Path(__file__).resolve().parents[2] / "config" / "urls.py"


def plugin_with(*defs):
    return mock.AsyncMock(return_value=list(defs))


DATASET = InputDefinition(name="dataset", label="Dataset", input_type=InputType.DATASET, required=True)


@override_settings(AISC_DEPLOYMENT="configurator")
class TheTargetInput(SimpleTestCase):
    def definitions(self, *defs):
        with mock.patch.object(target_input, "frozen_input_definitions", plugin_with(*defs)), \
             mock.patch.object(target_input, "ensure_system_target", mock.AsyncMock()) as ensured:
            out = async_to_sync(target_input.input_definitions_with_target)(mock.Mock(), uuid.uuid4())
        return out, ensured

    def test_en1_every_plugin_ends_with_the_required_target_input(self):
        out, _ = self.definitions(DATASET)
        self.assertEqual([d.name for d in out], ["dataset", "target"])
        target = out[-1]
        self.assertEqual((target.input_type, target.required), (InputType.RESOURCE, True))
        self.assertIn("system", target.label.lower())

    def test_en2_a_plugin_that_declares_target_is_not_given_a_second(self):
        own = InputDefinition(name="target", label="Mine", input_type=InputType.RESOURCE, required=True)
        out, _ = self.definitions(DATASET, own)
        self.assertEqual([d.name for d in out], ["dataset", "target"])
        self.assertEqual(out[-1].label, "Mine")

    def test_en2_a_plugins_own_optional_target_is_required_here(self):
        # O2: every evaluation names a target; a plugin's own declaration (optional, so standalone
        # keeps working) is made required in configurator mode
        own = InputDefinition(name="target", label="Mine", input_type=InputType.RESOURCE, required=False)
        out, _ = self.definitions(DATASET, own)
        self.assertEqual([(d.name, d.required, d.label) for d in out], [("dataset", True, "Dataset"), ("target", True, "Mine")])

    def test_en1_the_system_target_is_made_sure_of_so_there_is_always_one_to_pick(self):
        _, ensured = self.definitions(DATASET)
        ensured.assert_awaited_once()

    def test_en1_the_system_target_failing_never_hides_the_definitions(self):
        with mock.patch.object(target_input, "frozen_input_definitions", plugin_with(DATASET)), \
             mock.patch.object(target_input, "ensure_system_target", mock.AsyncMock(side_effect=RuntimeError("db"))):
            out = async_to_sync(target_input.input_definitions_with_target)(mock.Mock(), uuid.uuid4())
        self.assertEqual([d.name for d in out], ["dataset", "target"])


@override_settings(AISC_DEPLOYMENT="standalone")
class StandaloneIsUnchanged(SimpleTestCase):
    def test_no_target_input_in_standalone(self):
        with mock.patch.object(target_input, "frozen_input_definitions", plugin_with(DATASET)):
            out = async_to_sync(target_input.input_definitions_with_target)(mock.Mock(), uuid.uuid4())
        self.assertEqual([d.name for d in out], ["dataset"])


class Mounting(SimpleTestCase):
    def test_en1_the_route_is_mounted_in_front_of_the_plugin_router_in_configurator_mode_only(self):
        text = URLS.read_text()
        branch = text.index("if deployment.is_configurator():")
        mounted = text.index('v1_router.add_router("/plugins", target_input_router)')
        frozen = text.index('v1_router.add_router("/plugins", plugin_router)')
        self.assertLess(branch, mounted)
        self.assertLess(mounted, frozen)
        self.assertIn('v1_router.add_router("/projects/for-platform", platform_project_router)',
                      text[branch:mounted + 200])

    def test_en4_the_frozen_handler_is_reused_not_copied(self):
        from aisc_backend.routers import plugin
        self.assertIs(target_input.frozen_input_definitions, plugin.get_plugin_input_definitions)


class SystemTargetValue(SimpleTestCase):
    def test_the_mirror_is_found_by_the_platforms_value(self):
        pid = uuid.uuid4()
        self.assertEqual(target_input.system_reference(pid), f"target:{pid}/system")


if __name__ == "__main__":
    unittest.main()


from django.test import TestCase  # noqa: E402


class TheSystemTargetIsMadeOnce(TestCase):
    def test_made_once_as_a_resource_the_platform_finds_by_its_value(self):
        from aisc_backend.models import AIComponent, AISystem, Project
        platform_pid = uuid.uuid4()
        project = Project.objects.create(name="MCAS", platform_project_id=platform_pid)
        system = AISystem.objects.filter(project=project).first() or AISystem.objects.create(project=project, name="MCAS")
        async_to_sync(target_input.ensure_system_target)()
        async_to_sync(target_input.ensure_system_target)()
        made = AIComponent.objects.filter(system=system, json_value__value=f"target:{platform_pid}/system")
        self.assertEqual(made.count(), 1)
        self.assertEqual((made[0].name, made[0].component_type), ("Target · System: MCAS", "resource"))


class WhoAnswersTheForm(SimpleTestCase):
    """The mode is read once, at start: a child process per mode resolves the form's URL."""

    PROBE = (
        "import os, uuid, django\n"
        "os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')\n"
        "django.setup()\n"
        "from django.urls import resolve\n"
        "m = resolve(f'/api/v1/plugins/{uuid.uuid4()}/input_definitions')\n"
        "views = [c.cell_contents for c in (m.func.__closure__ or []) if hasattr(c.cell_contents, 'operations')]\n"
        "print([op.view_func.__module__ + '.' + op.view_func.__name__ for v in views for op in v.operations])\n"
    )

    def handler(self, **env):
        import os
        import subprocess
        import sys
        run = subprocess.run([sys.executable, "-c", self.PROBE], cwd=URLS.parents[1], capture_output=True,
                             text=True, env={**os.environ, **env}, timeout=120)
        self.assertEqual(run.returncode, 0, run.stderr[-800:])
        return run.stdout.strip().splitlines()[-1]

    def test_en1_configurator_answers_from_this_module(self):
        self.assertIn("target_input.input_definitions_with_target",
                      self.handler(AISC_DEPLOYMENT="configurator", DB_ENGINE="django.db.backends.postgresql"))

    def test_en1_standalone_answers_from_seans_route(self):
        self.assertIn("routers.plugin.get_plugin_input_definitions", self.handler(AISC_DEPLOYMENT="standalone"))
