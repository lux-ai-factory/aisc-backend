import uuid

from a4s_backend.models import Evaluation
from a4s_backend.repositories.base_repository import BaseRepository


class EvaluationRepository(BaseRepository[Evaluation]):

    def __init__(self):
        super().__init__(Evaluation)

    async def get_with_related(self, evaluation_pid: uuid.UUID) -> Evaluation:
        return await (Evaluation.objects
                            .select_related("dataset")
                            .select_related("dataset__datashape")
                            .select_related("dataset__datashape__date_feature")
                            .select_related("dataset__datashape__target_feature")
                            .prefetch_related("dataset__datashape__features")
                            .prefetch_related("observations")
                            .aget(pid=evaluation_pid))

    async def get_including(self, evaluation_pid: uuid.UUID, include: str) -> Evaluation:
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

        return await evaluation_objects.aget(pid=evaluation_pid)
