from ninja import ModelSchema
from pydantic import field_serializer

from a4s_backend.models import Feature


class FeatureOutScheme(ModelSchema):
    @field_serializer("feature_type", check_fields=False)
    def lowercase_feature_type(self, feature_type: str, _info):
        return feature_type.lower() if feature_type else feature_type

    class Meta:
        model = Feature
        exclude = ["datashape"]

class FeatureInScheme(ModelSchema):
    class Meta:
        model = Feature
        exclude = ["datashape"]
        fields_optional = "__all__"