"""
Assessment coverage (B4), under /projects/{pid}/assessment-coverage.

One read-only endpoint mapping each control objective to the installed
checklists and enabled plugins that could cover it (indicative keyword
matching, see services.assessment_coverage) plus its priority (key/score/rank
from the same `prioritise` service the matrix reads) and manual risk-mapping
count. The Overview page renders "how they cover the control objectives" from
this; nothing here is stored.
"""
import uuid

from ninja import Router

from aisc_backend.auth.access import role_or_404
from aisc_backend.models.project import Project
from aisc_backend.repositories.project_repository import ProjectRepository
from aisc_backend.schemas.assessment import AssessmentCoverageOut
from aisc_backend.services import assessment_coverage

router = Router(tags=["assessment"])

project_repository = ProjectRepository()


@router.get("/{pid}/assessment-coverage", response=AssessmentCoverageOut)
async def get_assessment_coverage(request, pid: uuid.UUID):
    project: Project = await project_repository.get(pid)
    await role_or_404(request, project)
    return await assessment_coverage.assessment_coverage(project)
