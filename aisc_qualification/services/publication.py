"""
Explicit, immutable publication of an assessed system (VER-01, slice 2).

A project has ONE assessed system: the core `AISystem`, reached through the
qualification `System` (slice 1). This module owns the ONE operation that turns
an editable draft into a *published snapshot* of that system: `publish` cuts the
next `SystemVersion` and freezes the qualification row, in a single transaction.

Nothing cuts a version implicitly. Saving answers stores a draft; only an
explicit publish (or an explicit card edit, which freezes a successor) produces
a version. Once published a snapshot is immutable — the model refuses to update
or delete it — so reopening or editing it yields a draft/successor and an old
version stays readable by its own id even after a later draft exists.
"""
from __future__ import annotations

from asgiref.sync import sync_to_async
from django.db import transaction
from django.utils import timezone

from aisc_backend.models.system import System, SystemVersion
from aisc_qualification.models import Qualification
from aisc_backend.repositories.system_repository import SystemRepository


class AlreadyPublished(RuntimeError):
    """The qualification is already a published snapshot."""


def _publish_sync(
    qualification_pk,
    system_pk,
    *,
    card,
    answers,
    created_by,
    name,
    version,
    provider,
    description,
    questionnaire_version,
    successor,
):
    """Cut the next version and freeze a snapshot, in one transaction.

    The `System` row is locked so two publishes cannot read the same "latest"
    and cut the same number: one explicit action, exactly one version. With
    `successor` the edited answers/card become a NEW published row and the source
    is left untouched; otherwise the source draft itself is frozen.
    """
    with transaction.atomic():
        system = System.objects.select_for_update().get(pk=system_pk)
        source = Qualification.objects.select_for_update().get(pk=qualification_pk)
        last = SystemVersion.objects.filter(system=system).order_by("-number").first()
        number = (last.number + 1) if last is not None else 1
        row = SystemVersion.objects.create(
            system=system,
            project_id=system.project_id,
            number=number,
            name=name,
            version=version,
            provider=provider,
            description=description,
            created_by=created_by,
        )
        if successor:
            published = Qualification.objects.create(
                project_id=source.project_id,
                system_version=row,
                questionnaire_id=source.questionnaire_id,
                questionnaire_version=(
                    questionnaire_version
                    if questionnaire_version is not None
                    else source.questionnaire_version
                ),
                answers=answers if answers is not None else source.answers,
                system_card=card,
                status="completed",
                published_at=timezone.now(),
            )
        else:
            if source.is_published:
                raise AlreadyPublished("this qualification is already published")
            source.system_card = card
            source.system_version = row
            source.status = "completed"
            source.published_at = timezone.now()
            if answers is not None:
                source.answers = answers
            if questionnaire_version is not None:
                source.questionnaire_version = questionnaire_version
            source.save(
                update_fields=[
                    "system_card",
                    "system_version",
                    "status",
                    "published_at",
                    "answers",
                    "questionnaire_version",
                ]
            )
            published = source
    return published.pk, row.pk


async def publish(
    project,
    qualification: Qualification,
    *,
    card: dict,
    created_by: str = "",
    answers: dict | None = None,
    questionnaire_version: int | None = None,
    successor: bool = False,
    name: str | None = None,
    version: str = "",
    provider: str = "aisc-qualification",
    description: str = "",
) -> tuple[Qualification, SystemVersion]:
    """Publish an assessed-system version (the ONE operation that cuts one).

    Resolves the project's one assessed system through the qualification content
    (`SystemRepository.get_or_create` links it to the core `AISystem`), then cuts
    the version and freezes the snapshot atomically. Publishes the draft in place
    by default; with `successor` it freezes edited answers as a NEW snapshot and
    leaves `qualification` untouched. Raises `AlreadyPublished` when publishing a
    row that is already a published snapshot.
    """
    system = await SystemRepository().get_or_create(project)
    published_pk, version_pk = await sync_to_async(_publish_sync)(
        qualification.pk,
        system.pk,
        card=card,
        answers=answers,
        created_by=created_by,
        name=name or project.name,
        version=version,
        provider=provider,
        description=description,
        questionnaire_version=questionnaire_version,
        successor=successor,
    )
    published = await Qualification.objects.aget(pk=published_pk)
    row = await SystemVersion.objects.aget(pk=version_pk)
    return published, row
