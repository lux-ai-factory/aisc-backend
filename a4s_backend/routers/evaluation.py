import datetime
import uuid

from ninja import Router, Schema

from a4s_backend.models.feature import Feature
from a4s_backend.models.observation import Observation
from a4s_backend.models.metric import Measurement, Metric

from a4s_backend.models.evaluation import Evaluation, EvaluationStatus
from a4s_backend.models.model import Model
from a4s_backend.repositories.base_repository import BaseRepository
from a4s_backend.repositories.dataset_repository import DatasetRepository
from a4s_backend.repositories.evaluation_repository import EvaluationRepository
from a4s_backend.repositories.project_repository import ProjectRepository
from a4s_backend.schemas.evaluation import EvaluationDetailOutSchema, EvaluationByStatusResponseSchema, \
    EvaluationOutSchema
from a4s_backend.schemas.measure import MeasureInSchema, MeasureOutSchema
from a4s_backend.services import a4s_eval

router = Router(tags=["evaluations"])

evaluation_repository = EvaluationRepository()
project_repository = ProjectRepository()
dataset_repository = DatasetRepository()
model_repository = BaseRepository(model=Model)
observation_repository = BaseRepository(model=Observation)
measurement_repository = BaseRepository(model=Measurement)
metric_repository = BaseRepository(model=Metric)
feature_repository = BaseRepository(model=Feature)


@router.get("/{evaluation_pid}", response=EvaluationDetailOutSchema)
async def get_evaluation_details(request, evaluation_pid: uuid.UUID, include: str):
    return await evaluation_repository.get_including(evaluation_pid, include)


@router.get("", response=list[EvaluationByStatusResponseSchema])
async def get_evaluations_by_status(request, status: EvaluationStatus):
    return await evaluation_repository.filter(status=status)


@router.put("/{evaluation_pid}", response=str)
async def update_evaluation_status(request, evaluation_pid: uuid.UUID, status: EvaluationStatus):
    evaluation = await evaluation_repository.get(evaluation_pid)

    evaluation.status = status
    await evaluation_repository.save(evaluation)

    return evaluation.status


@router.post("", response=EvaluationOutSchema)
async def create_evaluation(request, project_pid: uuid.UUID, model_pid: uuid.UUID, test_dataset_pid: uuid.UUID):
    project = await project_repository.get(project_pid)
    model = await model_repository.get(model_pid)
    dataset = await dataset_repository.get(test_dataset_pid)

    evaluation = Evaluation(
        project=project,
        model=model,
        dataset=dataset,
        status=EvaluationStatus.Pending
    )

    await evaluation_repository.save(evaluation)
    await a4s_eval.trigger_evaluation_task()

    return evaluation


@router.post("/{evaluation_pid}/measures", response={201: Schema})
async def create_evaluation_measures(request, evaluation_pid: uuid.UUID, data: list[MeasureInSchema]):
    evaluation = await evaluation_repository.get(evaluation_pid, True)

    # Get or create Observation associated with Evaluation
    if not evaluation.get_observations():
        observation = Observation(
            observer="A4S System",
            tool="A4S",
            whenObserved=datetime.datetime.now(),
            evaluation=evaluation
        )
        await observation_repository.save(observation)
    else:
        observation = evaluation.get_observations()[:1][0]


    for measure_in_schema in data:
        if measure_in_schema.feature_pid is None or measure_in_schema.feature_pid in [f.pid for f in evaluation.dataset.get_datashape().get_features()]:
            # Match measurements to features
            if measure_in_schema.feature_pid is not None:
                feature = await feature_repository.get(measure_in_schema.feature_pid)
                measure_in_schema.feature = feature
            measure_in_schema.description = ""

            # Get or create Metric from Measure name
            metric = await metric_repository.get_one(name=measure_in_schema.name)
            if not metric:
                metric = await metric_repository.save(Metric(name=measure_in_schema.name))

            # Save Measure associated to Observation
            measure_in_schema.observation = observation
            measure_in_schema.metric = metric
            await metric_repository.create(**measure_in_schema.dict(exclude={'feature_pid'}))

    return Schema()


@router.get("/{evaluation_pid}/measures", response=list[MeasureOutSchema])
async def get_evaluation_measures(request, evaluation_pid: uuid.UUID, name: str):
    evaluation = await evaluation_repository.get(evaluation_pid)
    observation = await evaluation.observations.order_by("-whenObserved").afirst()

    measurements = await measurement_repository.filter_with_related(name=name, observation=observation)

    return measurements
