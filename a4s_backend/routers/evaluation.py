import datetime
import logging
import uuid

from ninja import Router, ModelSchema, Field, Schema
from ninja.errors import HttpError

from a4s_backend.models.feature import Feature
from a4s_backend.models.observation import Observation
from a4s_backend.models.metric import Measurement, Metric

from a4s_backend.models.evaluation import Evaluation, EvaluationStatus
from a4s_backend.models.project import Project
from a4s_backend.models.dataset import Dataset
from a4s_backend.models.model import Model
from a4s_backend.schemas.dataset import DatasetPidOutScheme
from a4s_backend.schemas.feature import FeatureOutScheme
from a4s_backend.schemas.model import ModelPidOutScheme
from a4s_backend.schemas.project import ProjectOutSchema
from a4s_backend.services import a4s_eval

router = Router(tags=["evaluations"])

logger = logging.getLogger("django")


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


@router.get("/{evaluation_pid}", response=EvaluationDetailOutSchema)
async def get_evaluation_detail_by_pid(request, evaluation_pid: uuid.UUID, include: str):
    include_list = include.strip().split(",")

    evaluation_objects = Evaluation.objects
    if "project" in include_list:
        evaluation_objects = evaluation_objects.select_related("project")

    if "model" in include_list:
        evaluation_objects = evaluation_objects.select_related("model")
        evaluation_objects = evaluation_objects.select_related("model__dataset")
        evaluation_objects = evaluation_objects.select_related("model__dataset__datashape")
        evaluation_objects = evaluation_objects.select_related("model__dataset__datashape__date_feature")
        evaluation_objects = evaluation_objects.select_related("model__dataset__datashape__target_feature")
        evaluation_objects = evaluation_objects.prefetch_related("model__dataset__datashape__features")

    if "dataset" in include_list:
        evaluation_objects = evaluation_objects.select_related("dataset")
        evaluation_objects = evaluation_objects.select_related("dataset__datashape")
        evaluation_objects = evaluation_objects.select_related("dataset__datashape__date_feature")
        evaluation_objects = evaluation_objects.select_related("dataset__datashape__target_feature")
        evaluation_objects = evaluation_objects.prefetch_related("dataset__datashape__features")

    if "datashape" in include_list:
        evaluation_objects = evaluation_objects.select_related("project__expected_datashape")

    evaluation = await evaluation_objects.aget(pid=evaluation_pid)

    logger.info(evaluation)
    return evaluation


@router.get("", response=list[EvaluationByStatusResponseSchema])
async def get_evaluations_by_status(request, status: EvaluationStatus):
    evaluations = [e async for e in Evaluation.objects.filter(status=status).all()]
    logger.warning(evaluations)
    return evaluations


@router.put("/{evaluation_pid}", response=str)
async def update_evaluation_status(request, evaluation_pid: uuid.UUID, status: EvaluationStatus):
    evaluation = await Evaluation.objects.aget(pid=evaluation_pid)

    if not evaluation:
        raise HttpError(404, f"Evaluation {evaluation_pid} not found")

    evaluation.status = status
    await evaluation.asave()

    logger.info(evaluation)
    return evaluation.status


@router.post("", response=EvaluationOutSchema)
async def create_evaluation(request, project_pid: uuid.UUID, model_pid: uuid.UUID, test_dataset_pid: uuid.UUID):
    project = await Project.objects.aget(pid=project_pid)
    model = await Model.objects.aget(pid=model_pid)
    dataset = await Dataset.objects.aget(pid=test_dataset_pid)

    if not project or not model or not dataset or not project:
        raise HttpError(404, f"Project, Model or Dataset not found")

    evaluation = Evaluation(
        project=project,
        model=model,
        dataset=dataset,
        status=EvaluationStatus.Pending
    )

    await evaluation.asave()
    await a4s_eval.trigger_evaluation_task()

    logger.info(evaluation)
    return evaluation


class MeasureInSchema(ModelSchema):
    feature_pid: uuid.UUID | None = Field(default=None, alias="feature_pid")

    class Meta:
        model = Measurement
        fields = "__all__"
        fields_optional = "__all__"


@router.post("/{evaluation_pid}/measures", response={201: Schema})
async def create_measure(request, evaluation_pid: uuid.UUID, data: list[MeasureInSchema]):
    evaluation = await (Evaluation.objects
                        .select_related("dataset")
                        .select_related("dataset__datashape")
                        .select_related("dataset__datashape__date_feature")
                        .select_related("dataset__datashape__target_feature")
                        .prefetch_related("dataset__datashape__features")
                        .prefetch_related("observations")
                        .aget(pid=evaluation_pid))

    if not evaluation:
        raise HttpError(404, f"Evaluation {evaluation_pid} not found")


    # Get or create Observation associated with Evaluation
    if not evaluation.get_observations():
        observation = Observation(
            observer="A4S System",
            tool="A4S",
            whenObserved=datetime.datetime.now(),
            evaluation=evaluation
        )
        await observation.asave()
    else:
        observation = evaluation.get_observations()[:1][0]


    for measure_in_schema in data:
        if measure_in_schema.feature_pid is None or measure_in_schema.feature_pid in [f.pid for f in evaluation.dataset.get_datashape().get_features()]:
            # Match measurements to features
            if measure_in_schema.feature_pid is not None:
                feature = await Feature.objects.aget(pid=measure_in_schema.feature_pid)
                measure_in_schema.feature = feature
            measure_in_schema.description = ""

            # Get or create Metric from Measure name
            metric = await Metric.objects.filter(name=measure_in_schema.name).afirst()
            if not metric:
                metric = Metric(name=measure_in_schema.name)
                await metric.asave()

            # Save Measure associated to Observation
            measure_in_schema.observation = observation
            measure_in_schema.metric = metric
            await Measurement.objects.acreate(**measure_in_schema.dict(exclude={'feature_pid'}))

    return Schema()


class MeasureOutSchema(ModelSchema):
    feature: FeatureOutScheme | None

    class Meta:
        model = Measurement
        fields = ["name", "score", "time", "feature"]

@router.get("/{evaluation_pid}/measures", response=list[MeasureOutSchema])
async def project_evaluations(request, evaluation_pid: uuid.UUID, name: str):
    evaluation = await Evaluation.objects.aget(pid=evaluation_pid)
    observation = await evaluation.observations.order_by("-whenObserved").afirst()

    if evaluation is None:
        raise HttpError(404, f"Evaluation {evaluation_pid} not found")

    measurements = [m async for m in Measurement.objects.filter(name=name, observation=observation).select_related("feature").all()]

    return measurements
