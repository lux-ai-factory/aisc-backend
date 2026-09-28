import uuid
from unittest.mock import patch

from ninja.testing import TestAsyncClient

from aisc_backend.models import EvaluationStatus
from aisc_backend.routers.evaluation import router
from aisc_backend.routers.internal import router as internal_router
from aisc_backend.schemas.evaluation import (
    EvaluationOutSchema,
    EvaluationDetailOutSchema,
    EvaluationByStatusResponseSchema,
)
from aisc_backend.schemas.measure import MeasureInSchema

client = TestAsyncClient(router)
internal_client = TestAsyncClient(internal_router)

# The internal router is gated by InternalSharedKeyAuth, which reads this env var
# at request time; callers must present the same value as X-Internal-Secret.
INTERNAL_SECRET = "test-internal-secret"


async def create_evaluation(project_pid: uuid.UUID, plugins_to_run: list[dict]) -> EvaluationOutSchema:
    response = await client.post(
        '/task',
        json={"project_pid": str(project_pid), "plugins_to_run": plugins_to_run},
    )
    return EvaluationOutSchema.model_construct(**response.data)


async def get_evaluations_by_status(evaluation_status: EvaluationStatus) -> list[EvaluationByStatusResponseSchema]:
    response = await client.get(f'?status={evaluation_status}')
    evaluations = [EvaluationByStatusResponseSchema.model_construct(**item) for item in response.data]
    return evaluations


async def update_evaluation_status(pid: uuid.UUID, evaluation_status: EvaluationStatus) -> EvaluationStatus:
    with patch.dict("os.environ", {"INTERNAL_API_KEY": INTERNAL_SECRET}):
        response = await internal_client.put(
            f'/evaluations/{pid}?status={evaluation_status}',
            headers={"X-Internal-Secret": INTERNAL_SECRET},
        )
    return EvaluationStatus(response.data)


async def get_evaluation_including(pid: uuid.UUID, include_str: str) -> EvaluationDetailOutSchema:
    response = await client.get(f'/{pid}?include={include_str}')
    return EvaluationDetailOutSchema.model_construct(**response.data)


async def create_evaluation_measures(
    pid: uuid.UUID,
    measures_by_plugin: dict[uuid.UUID, list[MeasureInSchema]],
):
    body = {
        str(plugin_pid): [m.model_dump(mode="json") for m in measures]
        for plugin_pid, measures in measures_by_plugin.items()
    }
    with patch.dict("os.environ", {"INTERNAL_API_KEY": INTERNAL_SECRET}):
        response = await internal_client.post(
            f'/evaluations/{pid}/measures',
            json=body,
            headers={"X-Internal-Secret": INTERNAL_SECRET},
        )
    return response


async def get_evaluation_metric_names(pid: uuid.UUID) -> list[str]:
    response = await client.post(f'/{pid}/measurements/metric-names', json={})
    return response.data["names"]
