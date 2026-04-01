from __future__ import annotations

from ninja import ModelSchema, Schema

from a4s_backend.models.artifact import Artifact

class ArtifactPreviewSchema(Schema):
    type: str
    data: list | str | None = None


class ArtifactOutSchema(ModelSchema):
    preview: ArtifactPreviewSchema | None = None

    class Meta:
        model = Artifact
        fields = ["name", "data"]
