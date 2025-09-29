from ninja import Router, ModelSchema
from ninja_jwt.authentication import JWTAuth
#from django.shortcuts import get_object_or_404

from typing import List
from .models import Project


class ProjectInSchema(ModelSchema):
    class Meta:
        model = Project
        fields = ["name"]

class ProjectOutSchema(ModelSchema):
    class Meta:
        model = Project
        fields = ["id", "name"]


router = Router(tags=["projects"], auth=JWTAuth())


@router.post("/", response=ProjectInSchema)
def create_project(request, data: ProjectInSchema):
    user = request.user
    project = Project(name=data.name, owner=user)
    project.save()
    return project

@router.get("/", response=List[ProjectOutSchema])
def list_projects(request):
    user = request.user
    projects = Project.objects.filter(owner=user)
    return projects
