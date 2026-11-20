from django.contrib import admin

from aisc_evaluation.models import Evaluation, Measurement, Observation


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
