import uuid

from ninja import ModelSchema, Field

from a4s_backend.models import Evaluation
from a4s_backend.schemas.dataset import DatasetPidOutScheme
from a4s_backend.schemas.model import ModelPidOutScheme
from a4s_backend.schemas.project import ProjectOutSchema


class EvaluationOutSchema(ModelSchema):
    class Meta:
        model = Evaluation
        fields = "__all__"


class EvaluationDetailOutSchema(ModelSchema):
    project: ProjectOutSchema
    dataset: DatasetPidOutScheme
    model: ModelPidOutScheme

    class Meta:
        model = Evaluation
        fields = "__all__"
        fields_optional = "__all__"


class EvaluationByStatusResponseSchema(ModelSchema):
    evaluation_pid: uuid.UUID = Field(default=None, alias="pid")
    project_id: int = Field(default=None, alias="project_id")
    model_id: int = Field(default=None, alias="model_id")
    test_dataset_id: int = Field(default=None, alias="dataset_id")

    class Meta:
        model = Evaluation
        fields = ["id"]
