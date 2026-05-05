from django.contrib import admin

from vera_backend.models import Plugin
from vera_backend.models.dataset import Dataset
from vera_backend.models.model import Model
from vera_backend.models.evaluation import Evaluation
from vera_backend.models.observation import Observation
from vera_backend.models.project import Project
from vera_backend.models.measure import Measurement

@admin.register(Project)
class ProjectAdmin(admin.ModelAdmin):
    list_display = ["name", "status", "dataset_count", "model_count", "evaluation_count"]

    def dataset_count(self, obj):
        return len(obj.get_datasets())

    def model_count(self, obj):
        return len(obj.get_models())

    def evaluation_count(self, obj):
        return len(obj.get_evaluations())

@admin.register(Dataset)
class DatasetAdmin(admin.ModelAdmin):
    list_display = ["name", "project", "evaluation_count"]

    def evaluation_count(self, obj):
        return len(obj.get_evaluations())

@admin.register(Model)
class ModelAdmin(admin.ModelAdmin):
    list_display = ["name", "model_hub", "public", "evaluation_count"]

    def evaluation_count(self, obj):
        return len(obj.get_evaluations())

@admin.register(Evaluation)
class EvaluationAdmin(admin.ModelAdmin):
    list_display = ["pid", "status", "project", "observation_count"]

    def observation_count(self, obj):
        return len(obj.get_observations())

@admin.register(Observation)
class ObservationAdmin(admin.ModelAdmin):
    list_display = ["name", "observer", "tool", "created_at", "evaluation", "measurement_count"]

    def measurement_count(self, obj):
        return len(obj.get_measurements())

@admin.register(Measurement)
class MeasurementAdmin(admin.ModelAdmin):
    list_display = ["name", "unit", "time", "score", "error", "uncertainty", "observation", "metric", "feature"]

@admin.register(Plugin)
class PluginAdmin(admin.ModelAdmin):
    list_display = ["pid", "name", "project"]
