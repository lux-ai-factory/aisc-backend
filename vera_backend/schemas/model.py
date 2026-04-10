from ninja import ModelSchema

from vera_backend.models.model import Model


class ModelPidOutSchema(ModelSchema):
    class Meta:
        model = Model
        fields = ["pid"]

class ModelOutSchema(ModelSchema):
    class Meta:
        model = Model
        fields = ["pid", "name", "data"]

class ModelInSchema(ModelSchema):
    class Meta:
        model = Model
        fields = ["name"]