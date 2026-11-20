from django.contrib import admin

from aisc_plugins.models import Plugin


@admin.register(Plugin)
class PluginAdmin(admin.ModelAdmin):
    list_display = ["pid", "name", "project"]
