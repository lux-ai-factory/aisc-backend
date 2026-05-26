import uuid
from unittest.mock import patch, AsyncMock

from django.http import HttpResponse
from ninja.responses import Response
from ninja.testing import TestAsyncClient

from aisc_backend.models import EvaluationStatus
from aisc_backend.routers.evaluation import router
from aisc_backend.schemas.evaluation import EvaluationOutSchema, EvaluationDetailOutSchema, \
    EvaluationByStatusResponseSchema
from aisc_backend.schemas.measure import MeasureOutSchema, MeasureInSchema
from aisc_backend.schemas.project import ProjectDetailsOutSchema

client = TestAsyncClient(router)


async def create_evaluation(project_details: ProjectDetailsOutSchema, model_name: str, training_dataset_name: str) -> EvaluationOutSchema:
    with (
        patch("aisc_backend.routers.evaluation.aisc_backend.trigger_evaluation_task", new_callable=AsyncMock) as eval_response,
    ):
        eval_response.return_value = HttpResponse(status=200)

        model = next(m for m in project_details.models if m['name'] == model_name)
        training_dataset = next(d for d in project_details.datasets if d['name'] == training_dataset_name)

        response = await client.post(f'?project_pid={project_details.pid}&model_pid={model['pid']}&test_dataset_pid={training_dataset['pid']}')
        evaluation = EvaluationOutSchema.model_construct(**response.data)
        return evaluation


async def get_evaluations_by_status(evaluation_status: EvaluationStatus) -> list[EvaluationByStatusResponseSchema]:
    response = await client.get(f'?status={evaluation_status}')
    evaluations = [EvaluationByStatusResponseSchema.model_construct(**item) for item in response.data]
    return evaluations


async def update_evaluation_status(pid: uuid.UUID, evaluation_status: EvaluationStatus) -> EvaluationStatus:
    response = await client.put(f'/{pid}?status={evaluation_status}')
    return EvaluationStatus(response.data)


async def get_evaluation_including(pid: uuid.UUID, include_str: str) -> EvaluationDetailOutSchema:
    response = await client.get(f'/{pid}?include={include_str}')
    evaluation = EvaluationDetailOutSchema.model_construct(**response.data)
    return evaluation


async def create_evaluation_measures(pid: uuid.UUID, measures: list[MeasureInSchema]) -> Response:
    response = await client.post(f'/{pid}/measures', json=[m.dict() for m in measures])
    return response


async def get_evaluation_measures(pid: uuid.UUID, measure_name: str) -> list[MeasureOutSchema]:
    response = await client.get(f'/{pid}/measures?name={measure_name}')
    measures = [MeasureOutSchema.model_construct(**item) for item in response.data]
    return measures