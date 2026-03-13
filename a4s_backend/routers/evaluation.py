import datetime
import uuid

from django.core.exceptions import ObjectDoesNotExist
from ninja import Router, Schema

from a4s_backend.models import EvaluationPlugin
from a4s_backend.models.feature import Feature
from a4s_backend.models.observation import Observation
from a4s_backend.models.metric import Metric

from a4s_backend.models.evaluation import Evaluation, EvaluationStatus
from a4s_backend.models.model import Model
from a4s_backend.repositories.base_repository import BaseRepository
from a4s_backend.repositories.dataset_repository import DatasetRepository
from a4s_backend.repositories.evaluation_repository import EvaluationRepository
from a4s_backend.repositories.measurement_repository import MeasurementRepository
from a4s_backend.repositories.project_repository import ProjectRepository
from a4s_backend.routers.plugin import plugin_loader
from a4s_backend.schemas.evaluation import EvaluationDetailOutSchema, EvaluationByStatusResponseSchema, \
    EvaluationOutSchema
from a4s_backend.schemas.measure import MeasureInSchema, MeasureOutSchema
from a4s_backend.services import celery_service
from a4s_backend.auth import require_auth

router = Router(tags=["evaluation"])

evaluation_repository = EvaluationRepository()
project_repository = ProjectRepository()
dataset_repository = DatasetRepository()
model_repository = BaseRepository(model=Model)
observation_repository = BaseRepository(model=Observation)
measurement_repository = MeasurementRepository()
metric_repository = BaseRepository(model=Metric)
feature_repository = BaseRepository(model=Feature)
evaluation_plugin_repository = BaseRepository(model=EvaluationPlugin)


class EvaluationPluginInSchema(Schema):
    name: str
    dataset_pid: uuid.UUID | None = None
    model_pid: uuid.UUID | None = None


class CreateEvaluationRequest(Schema):
    project_pid: uuid.UUID
    plugins_to_run: list[EvaluationPluginInSchema]


@router.post("/task", response=EvaluationOutSchema, auth=[require_auth])
async def create_evaluation_task(request, data: CreateEvaluationRequest):
    project = await project_repository.get(data.project_pid, True)

    evaluation = Evaluation(status=EvaluationStatus.Pending, project=project,
                            user=request.user if request.user.is_authenticated else None)
    evaluation = await evaluation_repository.create(evaluation)

    evaluation_plugins = []
    for plugin_to_run in data.plugins_to_run:
        plugin_loader.load(plugin_to_run.name)
        plugin = next((p for p in project.get_enabled_plugins() if p.name == plugin_to_run.name), None)
        if plugin:

            dataset = None
            if plugin_to_run.dataset_pid is not None:
                dataset = await dataset_repository.get(plugin_to_run.dataset_pid)

            model = None
            if plugin_to_run.model_pid is not None:
                model = await model_repository.get(plugin_to_run.model_pid)

            run_plugin = EvaluationPlugin(plugin=plugin, evaluation=evaluation, dataset=dataset, model=model)
            run_plugin = await evaluation_plugin_repository.create(run_plugin)
            evaluation_plugins.append(run_plugin)

    evaluation_plugins_task = await celery_service.run_evaluation(evaluation.pid)

    evaluation.task = evaluation_plugins_task.task_id
    await evaluation_repository.save(evaluation)

    return evaluation


@router.get("/{evaluation_pid}", response=EvaluationDetailOutSchema)
async def get_evaluation_details(request, evaluation_pid: uuid.UUID, include: str):
    return await evaluation_repository.get_including(evaluation_pid, include)


@router.get("", response=list[EvaluationByStatusResponseSchema])
async def get_evaluations_by_status(request, status: EvaluationStatus):
    return await evaluation_repository.filter(status=status)


@router.put("/{evaluation_pid}", response=str)
async def update_evaluation_status(request, evaluation_pid: uuid.UUID, status: EvaluationStatus):
    evaluation = await evaluation_repository.get(evaluation_pid, True)

    evaluation.status = status
    await evaluation_repository.save(evaluation)

    return evaluation.status


@router.post("", response=EvaluationOutSchema, auth=[require_auth])
async def create_evaluation(request, project_pid: uuid.UUID, model_pid: uuid.UUID, test_dataset_pid: uuid.UUID):
    project = await project_repository.get(project_pid)
    model = await model_repository.get(model_pid)
    dataset = await dataset_repository.get(test_dataset_pid)

    evaluation = Evaluation(
        project=project,
        model=model,
        dataset=dataset,
        status=EvaluationStatus.Pending,
        user=request.user if request.user.is_authenticated else None,
    )

    await evaluation_repository.save(evaluation)
    await celery_service.run_evaluation_task()

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
            measure_in_schema.description = "" if measure_in_schema.description is None else measure_in_schema.description

            # Get or create Metric from Measure name
            try:
                metric = await metric_repository.get_one(name=measure_in_schema.name)
            except ObjectDoesNotExist:
                metric = await metric_repository.save(Metric(name=measure_in_schema.name))

            # Save Measure associated to Observation
            measure_in_schema.observation = observation
            measure_in_schema.metric = metric
            await measurement_repository.create(measure_in_schema.model_dump(exclude={'feature_pid'}))

    return Schema()


@router.get("/{evaluation_pid}/measures", response=list[MeasureOutSchema])
async def get_evaluation_measures(request, evaluation_pid: uuid.UUID, name: str):
    evaluation = await evaluation_repository.get(evaluation_pid)
    observation = await evaluation.observations.order_by("-whenObserved").afirst()

    measurements = await measurement_repository.filter_with_related(name=name, observation=observation)

    return measurements
