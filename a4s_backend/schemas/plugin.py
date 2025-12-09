from ninja import ModelSchema

from a4s_backend.models import Plugin


class PluginOutSchema(ModelSchema):

    class Meta:
        model = Plugin
        fields = ["pid", "name", "config"]
