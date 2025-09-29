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

from ninja import NinjaAPI, Router
from projects.api import router as projects_router
from ninja_jwt.routers.obtain import obtain_pair_router
from ninja_jwt.routers.verify import verify_router

api = NinjaAPI(title='Testing API')

api.add_router("/auth/token", obtain_pair_router, tags=["token"])
api.add_router("/auth/token", verify_router, tags=["token"])

v1_router = Router()
v1_router.add_router("/projects/", projects_router)
api.add_router("/v1/", v1_router)



urlpatterns = [
    path('admin/', admin.site.urls),

    #path("accounts/", include("allauth.urls")),
    path("_allauth/", include("allauth.headless.urls")),

    path('api/', api.urls),
]
