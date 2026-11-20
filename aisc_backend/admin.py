from django.contrib import admin

from aisc_backend.models import AIComponent, Project


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
