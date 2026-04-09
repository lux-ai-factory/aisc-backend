from typing import Any

from pydantic import BaseModel

from a4s_plugin_interface.models.measure import Measure
from a4s_plugin_interface.base_evaluation_plugin import BaseEvaluationPlugin, metric


# Define the configuration form schema
class ConfigFormSchema(BaseModel):
    pass


class {{ plugin_name }}(BaseEvaluationPlugin[ConfigFormSchema]):
    def evaluate(self, config_data: dict) -> Any:
        pass

    @metric("my-metric")
    def my_metric(self, evaluation_output: Any) -> list[Measure]:
        pass
