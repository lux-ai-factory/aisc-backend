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

from a4s_backend.routers.app import router as app_router
from a4s_backend.routers.project import router as project_router
from a4s_backend.routers.dataset import router as dataset_router
from a4s_backend.routers.model import router as model_router
from a4s_backend.routers.datashape import router as datashape_router
from a4s_backend.routers.evaluation import router as evaluation_router
from a4s_backend.routers.plugin import router as plugin_router
from a4s_backend.routers.task import router as task_router
from a4s_backend.routers.file import router as file_router

from a4s_backend.utils.logging_ninja_api import LoggingNinjaAPI
from a4s_backend.utils.exception_handlers import register_exception_handlers
from config.settings import APP_NAME

api = LoggingNinjaAPI(title=APP_NAME)
register_exception_handlers(api)

v1_router = Router()

v1_router.add_router("/app", app_router)
# Our endpoints
v1_router.add_router("/projects", project_router)
v1_router.add_router("/datasets", dataset_router)
v1_router.add_router("/models", model_router)
v1_router.add_router("/datashapes", datashape_router)
v1_router.add_router("/evaluations", evaluation_router)
v1_router.add_router("/plugins", plugin_router)
v1_router.add_router("/tasks", task_router)
v1_router.add_router("/files", file_router)

api.add_router("/v1/", v1_router)


urlpatterns = [
    path('admin/', admin.site.urls),

    path("_allauth/", include("allauth.headless.urls")),

    # OIDC callback URLs (OAuth2 redirect flow from Keycloak / any OIDC provider)
    path("accounts/", include("allauth.socialaccount.urls")),

    path('api/', api.urls),
]
