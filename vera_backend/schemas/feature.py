from ninja import ModelSchema

from vera_backend.models import Feature


class FeatureOutSchema(ModelSchema):
    class Meta:
        model = Feature
        exclude = ["datashape"]

class FeatureInSchema(ModelSchema):
    class Meta:
        model = Feature
        exclude = ["datashape"]
        fields_optional = "__all__"
