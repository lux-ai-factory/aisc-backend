"""
URL configuration for config project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/5.2/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""

from django.contrib import admin
from django.urls import path, include

from ninja import Router

from aisc_backend.routers.app import router as app_router
from aisc_backend.routers.project import router as project_router
from aisc_backend.routers.component import router as component_router
from aisc_backend.routers.evaluation import router as evaluation_router
from aisc_backend.routers.plugin import router as plugin_router
from aisc_backend.routers.task import router as task_router
from aisc_backend.routers.file import router as file_router
from aisc_backend.routers.stats import router as stats_router
from aisc_backend.routers.results import router as results_router
from aisc_backend.routers.me import router as me_router
from aisc_backend.routers.audit import router as audit_router
from aisc_backend.routers.authz import router as authz_router
from aisc_backend.routers.admin_users import router as admin_users_router
from aisc_backend.routers.catalogue import router as catalogue_router
from aisc_backend.routers.control import router as control_router
from aisc_backend.routers.control_objectives import router as control_objectives_router
from aisc_backend.routers.evidence import router as evidence_router
from aisc_backend.routers.connection import router as connection_router
from aisc_backend.routers.helper import router as helper_router
from aisc_backend.routers.allowed_host import router as allowed_host_router
from aisc_backend.routers.qualification import router as qualification_router
from aisc_backend.routers.questionnaire import router as questionnaire_router
from aisc_backend.routers.report import router as report_router
from aisc_backend.routers.assessment import router as assessment_router
from aisc_backend.routers.internal import router as internal_router
from aisc_backend.routers.project_config import router as project_config_router

from aisc_backend.auth.keycloak import KeycloakAuth
from aisc_backend.utils.logging_ninja_api import LoggingNinjaAPI
from aisc_backend.utils.exception_handlers import register_exception_handlers
from config.settings import APP_NAME

# Deny-by-default: KeycloakAuth() is the API-wide default, so EVERY endpoint requires a valid token.
# (KeycloakAuth lets everyone through while AUTH_ENABLED is False, so dev is unaffected; in prod with
# AUTH_ENABLED=true it enforces. Any new router is automatically protected — nothing can be forgotten.)
# The Swagger docs + openapi.json live under /api/ (not /api/v1/) and stay public.
api = LoggingNinjaAPI(title=APP_NAME, auth=KeycloakAuth())
register_exception_handlers(api)

v1_router = Router()

v1_router.add_router("/app", app_router)
# Our endpoints
v1_router.add_router("/projects", project_router)
v1_router.add_router("/projects", qualification_router)  # /{pid}/system-versions, /questionnaire, /qualification
v1_router.add_router("/projects", control_objectives_router)  # /{pid}/control-objectives/*
v1_router.add_router("/projects", evidence_router)  # /{pid}/evidence/* (versioned evidence map)
v1_router.add_router("/projects", results_router)  # /{pid}/results (scoped results + dashboard bridge)
v1_router.add_router("/projects", connection_router)  # /{pid}/connections/*, /{pid}/runs/{plugin}/bindings
v1_router.add_router("/projects", helper_router)  # /{pid}/helpers/* (helper models per role)
v1_router.add_router("/projects", allowed_host_router)  # /{pid}/allowed-hosts/* (internal-host allowlist)
v1_router.add_router("/projects", control_router)  # /{pid}/controls/* (library, submissions)
v1_router.add_router("/projects", report_router)  # /{pid}/reports/* (generate, pdf)
v1_router.add_router("/projects", assessment_router)  # /{pid}/assessment-coverage (B4)
v1_router.add_router("", catalogue_router)  # /catalogue + /projects/{pid}/catalogue/*
v1_router.add_router("/components", component_router)
v1_router.add_router("/evaluations", evaluation_router)
v1_router.add_router("/plugins", plugin_router)
v1_router.add_router("/tasks", task_router)
v1_router.add_router("/files", file_router)
v1_router.add_router("/stats", stats_router)
v1_router.add_router("/me", me_router)
v1_router.add_router("/audit", audit_router)
v1_router.add_router("/authz", authz_router)
v1_router.add_router("/admin/users", admin_users_router)  # admin-only Keycloak user mgmt
v1_router.add_router("/questionnaires", questionnaire_router)
v1_router.add_router("/project/settings", project_config_router)

api.add_router("/v1/", v1_router)
api.add_router("/v1/internal", internal_router)


urlpatterns = [
    path("admin/", admin.site.urls),
    path("_allauth/", include("allauth.headless.urls")),
    path("api/", api.urls),
]
