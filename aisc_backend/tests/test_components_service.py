"""
The component service: presets, slot compatibility and provenance (ADR 0009).

These are pure functions over an unsaved component and a slot, so they need no
database.
"""
from django.test import SimpleTestCase

from aisc_backend.models.ai_system import AIComponent, AIComponentType
from aisc_backend.services import components as component_service
from aisc_plugin_interface.models.evaluation_input import InputDefinition, InputType
from aisc_plugin_interface.adapters import BUILTIN_ADAPTERS


def _component(**fields):
    defaults = dict(name="comp", description="", data="")
    defaults.update(fields)
    return AIComponent(**defaults)


def _slot(name, accepts, *, adapter=None, adapter_required=False, role="input"):
    return InputDefinition(
        name=name, label=name.title(), input_type=InputType.LLM, accepts=accepts,
        adapter=adapter, adapter_required=adapter_required, role=role,
    )


class PresetTestCase(SimpleTestCase):
    def test_llm_preset_fills_columns_and_config(self):
        component = _component()
        component_service.apply_preset(
            component, "llm",
            {"url": "https://models.example/v1", "model": "gpt-4o", "secret": "sk"},
        )
        self.assertEqual(component.component_type, AIComponentType.ENDPOINT)
        self.assertEqual(component.protocol, "openai-chat")
        self.assertEqual(component.adapter, "openai")
        self.assertEqual(component.url, "https://models.example/v1")
        self.assertEqual(component.adapter_config, {"model": "gpt-4o"})
        self.assertEqual(component.preset, "llm")

    def test_read_preset_never_returns_the_secret(self):
        component = _component()
        component_service.apply_preset(
            component, "llm", {"url": "https://models.example/v1", "model": "m"}
        )
        read = component_service.read_preset(component)
        self.assertEqual(read["preset"], "llm")
        self.assertEqual(read["fields"]["url"], "https://models.example/v1")
        self.assertEqual(read["fields"]["model"], "m")
        # the secret field is not a component column and is never read back
        self.assertNotIn("secret", read["fields"])

    def test_unknown_preset_is_rejected(self):
        with self.assertRaises(ValueError):
            component_service.apply_preset(_component(), "nope", {})


class SlotFitTestCase(SimpleTestCase):
    def test_an_openai_endpoint_fits_an_openai_chat_slot(self):
        component = _component(
            component_type=AIComponentType.ENDPOINT, protocol="openai-chat",
        )
        fits, adapters = component_service.slot_fit(
            _slot("s", ["openai-chat"]), component, list(BUILTIN_ADAPTERS)
        )
        self.assertTrue(fits)
        self.assertEqual(adapters, ["openai"])

    def test_a_csv_dataset_fits_a_records_slot(self):
        component = _component(component_type=AIComponentType.DATASET, format="csv")
        fits, adapters = component_service.slot_fit(
            _slot("s", ["records"], role="input"), component, list(BUILTIN_ADAPTERS)
        )
        self.assertTrue(fits)
        self.assertEqual(adapters, ["csv"])

    def test_a_model_does_not_fit_a_records_slot(self):
        component = _component(component_type=AIComponentType.MODEL, format="onnx")
        fits, _ = component_service.slot_fit(
            _slot("s", ["records"], role="input"), component, list(BUILTIN_ADAPTERS)
        )
        self.assertFalse(fits)


class ResolveInputAdapterTestCase(SimpleTestCase):
    def test_the_first_fitting_adapter_is_the_default(self):
        component = _component(
            component_type=AIComponentType.ENDPOINT, protocol="openai-chat",
        )
        adapter, version, config = component_service.resolve_input_adapter(
            _slot("s", ["openai-chat"]), component, None, None, list(BUILTIN_ADAPTERS)
        )
        self.assertEqual(adapter, "openai")
        self.assertEqual(version, "1")
        self.assertEqual(config, {})

    def test_a_locked_slot_overrides_the_request(self):
        component = _component(
            component_type=AIComponentType.ENDPOINT, protocol="openai-chat",
        )
        adapter, _, _ = component_service.resolve_input_adapter(
            _slot("s", ["openai-chat"], adapter="openai", adapter_required=True),
            component, "a2a", None, list(BUILTIN_ADAPTERS),
        )
        self.assertEqual(adapter, "openai")

    def test_an_unfitting_request_is_refused(self):
        component = _component(
            component_type=AIComponentType.ENDPOINT, protocol="openai-chat",
        )
        with self.assertRaises(ValueError):
            component_service.resolve_input_adapter(
                _slot("s", ["openai-chat"]), component, "a2a", None,
                list(BUILTIN_ADAPTERS),
            )


class ProvenanceTestCase(SimpleTestCase):
    def test_provenance_has_no_secret_and_a_stable_hash(self):
        component = _component(
            component_type=AIComponentType.ENDPOINT, protocol="openai-chat",
            url="https://models.example/v1",
        )
        first = component_service.provenance_of(component, "openai", "1", {"model": "m"})
        second = component_service.provenance_of(component, "openai", "1", {"model": "m"})
        self.assertEqual(first, second)
        self.assertEqual(first["url"], "https://models.example/v1")
        self.assertEqual(first["adapter"], "openai")
        self.assertNotIn("secret", first)
        changed = component_service.provenance_of(component, "openai", "1", {"model": "other"})
        self.assertNotEqual(first["config_hash"], changed["config_hash"])
