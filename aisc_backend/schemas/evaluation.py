import uuid

from ninja import ModelSchema, Field

from aisc_backend.models import Evaluation

from aisc_backend.schemas.plugin import EvaluationPluginOutSchema, ShownEvaluationPluginOutSchema
from aisc_backend.schemas.project import ProjectOutSchema


class EvaluationOutSchema(ModelSchema):
    class Meta:
        model = Evaluation
        fields = "__all__"


class EvaluationDetailOutSchema(ModelSchema):
    project: ProjectOutSchema
    evaluation_plugins: list[EvaluationPluginOutSchema] | None = Field(default=None, alias="get_evaluation_plugins")

    class Meta:
        model = Evaluation
        fields = "__all__"
        fields_optional = "__all__"


class ShownEvaluationDetailOutSchema(EvaluationDetailOutSchema):
    """An evaluation as the public routes answer it: each plugin's configuration masked. The worker's
    internal route answers EvaluationDetailOutSchema, the stored configuration."""
    evaluation_plugins: list[ShownEvaluationPluginOutSchema] | None = Field(
        default=None, alias="get_evaluation_plugins")


class EvaluationByStatusResponseSchema(ModelSchema):
    evaluation_pid: uuid.UUID = Field(default=None, alias="pid")
    project_id: int = Field(default=None, alias="project_id")

    class Meta:
        model = Evaluation
        fields = ["id"]
