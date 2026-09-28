from django.contrib import admin

from aisc_backend.models import Plugin, AIComponent
from aisc_backend.models.evaluation import Evaluation
from aisc_backend.models.observation import Observation
from aisc_backend.models.project import Project
from aisc_backend.models.measure import Measurement

@admin.register(Project)
class ProjectAdmin(admin.ModelAdmin):
    list_display = ["name", "status", "component_count", "evaluation_count"]

    def component_count(self, obj):
        return len(obj.get_components())

    def evaluation_count(self, obj):
        return len(obj.get_evaluations())

@admin.register(AIComponent)
class AIComponentAdmin(admin.ModelAdmin):
    list_display = ["name", "component_type", "system", "evaluation_count"]

    def evaluation_count(self, obj):
        return len(obj.evaluations.all())

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
    list_display = ["name", "unit", "time", "score", "error", "uncertainty", "observation", "metric"]

@admin.register(Plugin)
class PluginAdmin(admin.ModelAdmin):
    list_display = ["pid", "name", "project"]
