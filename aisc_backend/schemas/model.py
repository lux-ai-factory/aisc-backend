from ninja import ModelSchema

from aisc_backend.models.model import Model


class ModelPidOutSchema(ModelSchema):
    class Meta:
        model = Model
        fields = ["pid"]

class ModelOutSchema(ModelSchema):
    class Meta:
        model = Model
        fields = ["pid", "name", "data", "file_size"]

class ModelInSchema(ModelSchema):
    class Meta:
        model = Model
        fields = ["name"]