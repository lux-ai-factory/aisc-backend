import uuid
from typing import Any

from aisc_backend.models import Evaluation
from aisc_backend.models.observation import Observation
from aisc_backend.repositories.base_repository import BaseRepository


def build_evaluation_queryset(include: str = "", include_all: bool = False):
    include_list = include.strip().split(",")

    evaluation_queryset = Evaluation.objects

    if "project" in include_list or include_all:
        evaluation_queryset = evaluation_queryset.select_related("project")

    if "plugin" in include_list or include_all:
        evaluation_queryset = evaluation_queryset.prefetch_related("evaluation_plugins")

        evaluation_queryset = evaluation_queryset.prefetch_related("evaluation_plugins__plugin_config")
        evaluation_queryset = evaluation_queryset.prefetch_related("evaluation_plugins__plugin_config__plugin")
        evaluation_queryset = evaluation_queryset.prefetch_related("evaluation_plugins__plugin_config__setting_mappings__project_config")

        evaluation_queryset = evaluation_queryset.prefetch_related("evaluation_plugins__evaluation_inputs")
        evaluation_queryset = evaluation_queryset.prefetch_related("evaluation_plugins__evaluation_inputs__component")
        evaluation_queryset = evaluation_queryset.prefetch_related("evaluation_plugins__evaluation_inputs__component__source_dataset")
        evaluation_queryset = evaluation_queryset.prefetch_related("evaluation_plugins__artifacts")

    return evaluation_queryset


class EvaluationRepository(BaseRepository[Evaluation]):

    def __init__(self):
        super().__init__(Evaluation)

    async def filter_with_related(
            self,
            filters: dict[str, Any] | None = None,
            exclude: dict[str, Any] | None = None
    ) -> list[Evaluation]:
        evaluation_queryset = build_evaluation_queryset("", True)
        evaluation_queryset = evaluation_queryset.prefetch_related("observations")

        if filters:
            evaluation_queryset = evaluation_queryset.filter(**filters)

        if exclude:
            evaluation_queryset = evaluation_queryset.exclude(**exclude)

        return [m async for m in evaluation_queryset.all()]

    async def get_with_related(self, evaluation_pid: uuid.UUID) -> Evaluation:
        evaluation_queryset = build_evaluation_queryset("", True)
        evaluation_queryset = evaluation_queryset.prefetch_related("observations")

        return await (evaluation_queryset.aget(pid=evaluation_pid))

    async def get_including(self, evaluation_pid: uuid.UUID, include: str, include_all: bool = False) -> Evaluation:
        evaluation_queryset = build_evaluation_queryset(include, include_all)
        # Always prefetch project since EvaluationDetailOutSchema requires it
        evaluation_queryset = evaluation_queryset.select_related("project")
        evaluation_queryset = evaluation_queryset.prefetch_related("observations")

        return await evaluation_queryset.aget(pid=evaluation_pid)

    async def latest_for_project(self, project) -> Evaluation | None:
        return await (
            Evaluation.objects.filter(project=project)
            .order_by("-created_at")
            .prefetch_related(
                "evaluation_plugins__plugin_config__plugin",
                "evaluation_plugins__evaluation_inputs__component",
            )
            .afirst()
        )

    async def get_with_plugin_inputs(self, evaluation_pid: uuid.UUID) -> Evaluation:
        return await (
            Evaluation.objects
            .prefetch_related("evaluation_plugins")
            .prefetch_related("evaluation_plugins__evaluation_inputs__component__system__project")
            .aget(pid=evaluation_pid)
        )

    async def get_latest_observation(self, evaluation: Evaluation, tool: str) -> Observation | None:
        return await (
            evaluation.observations.filter(tool=tool)
            .order_by("-created_at")
            .afirst()
        )

    async def plugin_status(self, evaluation: Evaluation) -> tuple[bool, int]:
        has_failed_plugins = await evaluation.evaluation_plugins.filter(status="Failed").aexists()
        total_plugins = await evaluation.evaluation_plugins.acount()
        return has_failed_plugins, total_plugins
