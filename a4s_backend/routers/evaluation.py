import datetime
import uuid

from asgiref.sync import sync_to_async
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ObjectDoesNotExist
from ninja import Router, Schema, Body
from ninja.errors import HttpError

from a4s_backend.models import EvaluationPlugin, Dataset, EvaluationPluginInputFile
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
from a4s_plugin_interface import InputType

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


class EvaluationPluginInputInSchema(Schema):
    pid: uuid.UUID
    name: str
    input_type: InputType

class EvaluationPluginInSchema(Schema):
    name: str
    inputs: list[EvaluationPluginInputInSchema] | None = None

class CreateEvaluationRequest(Schema):
    project_pid: uuid.UUID
    plugins_to_run: list[EvaluationPluginInSchema]


@router.post("/task", response=EvaluationOutSchema)
async def create_evaluation_task(request, data: CreateEvaluationRequest):
    project = await project_repository.get(data.project_pid, True)

    evaluation = Evaluation(status=EvaluationStatus.Pending, project=project)
    evaluation = await evaluation_repository.create(evaluation)

    evaluation_plugins = []
    for plugin_to_run in data.plugins_to_run:
        plugin_loader.load(plugin_to_run.name)
        plugin = next((p for p in project.get_enabled_plugins() if p.name == plugin_to_run.name), None)
        if plugin:
            plugin_config = plugin.current_config
            if plugin_config is None:
                raise HttpError(400, f"Plugin {plugin.name} has no current config")

            run_plugin = EvaluationPlugin(plugin_config=plugin_config, evaluation=evaluation)
            run_plugin = await evaluation_plugin_repository.create(run_plugin)

            # Link the input files
            if plugin_to_run.inputs:
                for input_data in plugin_to_run.inputs:
                    # Determine content type and model
                    if input_data.input_type == InputType.DATASET:
                        content_model = Dataset
                    elif input_data.input_type == InputType.MODEL:
                        content_model = Model
                    else:
                        continue

                    # Get the actual object (Dataset or Model) from DB
                    # Note: Ensure you have access to a repository or use the model's objects
                    content_obj = await content_model.objects.aget(pid=input_data.pid)
                    content_type = await sync_to_async(ContentType.objects.get_for_model)(content_model)


                    # Create the link
                    input_file = EvaluationPluginInputFile(
                        evaluation_plugin=run_plugin,
                        name=input_data.name,  # This matches the key in the @input decorator
                        content_type=content_type,
                        object_id=content_obj.id,
                        content_object=content_obj
                    )
                    await input_file.asave()

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


@router.post("/{evaluation_pid}/measures", response={201: Schema})
async def create_evaluation_measures(request, evaluation_pid: uuid.UUID, data: dict[str, list[MeasureInSchema]] = Body(...)):
    evaluation = await evaluation_repository.get(evaluation_pid, True)

    for plugin_name, measures in data.items():
        # Create a new Observation for each plugin
        observation = Observation(
            observer="A4S System",
            tool=plugin_name,
            whenObserved=datetime.datetime.now(),
            evaluation=evaluation
        )
        await observation_repository.save(observation)

        for measure_in_schema in measures:
            measure_in_schema.description = "" if measure_in_schema.description is None else measure_in_schema.description

            # Get or create Metric
            try:
                metric = await metric_repository.get_one(name=measure_in_schema.name)
            except ObjectDoesNotExist:
                metric = await metric_repository.save(Metric(name=measure_in_schema.name))

            # Save Measure associated with the Observation
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
