from typing import Type
from a4s_plugin_interface.input_providers.base_input_provider import BaseInputProvider
from a4s_plugin_interface.models.evaluation_input import InputDefinition, InputType


def evaluation_input(name: str, label: str, input_provider_class: Type[BaseInputProvider], input_type: InputType, required: bool = True):
    """
    Decorator to create input definitions and their provider.
    """

    def decorator(cls):
        if "_input_definitions" not in cls.__dict__:
            cls._input_definitions = []
        if "_input_provider_types" not in cls.__dict__:
            cls._input_provider_types = {}

        if not any(d.name == name for d in cls._input_definitions):
            cls._input_definitions.append(
                InputDefinition(name=name, label=label, input_type=input_type, required=required)
            )
        cls._input_provider_types[name] = input_provider_class
        return cls

    return decorator