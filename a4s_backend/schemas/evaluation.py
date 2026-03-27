import uuid

from ninja import ModelSchema, Field

from a4s_backend.models import Evaluation

from a4s_backend.schemas.plugin import EvaluationPluginOutSchema
from a4s_backend.schemas.project import ProjectOutSchema


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


class EvaluationByStatusResponseSchema(ModelSchema):
    evaluation_pid: uuid.UUID = Field(default=None, alias="pid")
    project_id: int = Field(default=None, alias="project_id")

    class Meta:
        model = Evaluation
        fields = ["id"]
