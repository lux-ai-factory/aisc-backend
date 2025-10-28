from ninja import ModelSchema

from a4s_backend.models import Feature


class FeatureOutScheme(ModelSchema):

    class Meta:
        model = Feature
        exclude = ["datashape"]

class FeatureInScheme(ModelSchema):
    class Meta:
        model = Feature
        exclude = ["datashape"]
        fields_optional = "__all__"
