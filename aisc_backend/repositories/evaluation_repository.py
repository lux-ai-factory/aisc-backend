import uuid
from typing import Any

from aisc_backend.models import Evaluation
from aisc_backend.repositories.base_repository import BaseRepository, T


def build_evaluation_queryset(include: str = "", include_all: bool = False):
    include_list = include.strip().split(",")

    evaluation_queryset = Evaluation.objects

    if "project" in include_list or include_all:
        evaluation_queryset = evaluation_queryset.select_related("project")

    if "plugin" in include_list or include_all:
        evaluation_queryset = evaluation_queryset.prefetch_related("evaluation_plugins")

        evaluation_queryset = evaluation_queryset.prefetch_related("evaluation_plugins__plugin_config")
        evaluation_queryset = evaluation_queryset.prefetch_related("evaluation_plugins__plugin_config__plugin")

        evaluation_queryset = evaluation_queryset.prefetch_related("evaluation_plugins__input_files")
        evaluation_queryset = evaluation_queryset.prefetch_related("evaluation_plugins__input_files__content_object")
        evaluation_queryset = evaluation_queryset.prefetch_related("evaluation_plugins__input_files__content_type")
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
