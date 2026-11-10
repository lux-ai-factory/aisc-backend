"""
Project membership as data, Django ORM edition.

Every query that decides "may this person see/change this project" goes through
here (or through `aisc_backend.auth.access`), so an endpoint cannot forget the
membership check. The role rules themselves live in
`aisc_backend.auth.membership`; this module only reads and writes the rows.

Semantics mirror the platform's membership queries: subject (Keycloak `sub`) is
the identity every decision keys on, `email` is display-only, and adding a
member is an upsert on (project, subject).
"""

from aisc_backend.models.project import Project
from aisc_backend.models.project_member import MembershipRole, ProjectMember


class MembershipRepository:
    async def role_in_project(self, project: Project, subject: str) -> str | None:
        """What this person is to this project, or None if they are not in it."""
        member = (
            await ProjectMember.objects.filter(project=project, subject=subject)
            .afirst()
        )
        return member.role if member is not None else None

    async def projects_for(self, subject: str) -> list[Project]:
        """Every project the subject is in (any role)."""
        return [
            m.project
            async for m in ProjectMember.objects.filter(subject=subject)
            .select_related("project")
            .order_by("-project__created_at")
        ]

    async def members(self, project: Project) -> list[dict]:
        return [
            {"subject": m.subject, "email": m.email, "role": m.role}
            async for m in ProjectMember.objects.filter(project=project).order_by("created_at")
        ]

    async def add_member(
        self, project: Project, subject: str, email: str | None, role: str
    ) -> dict:
        """Put somebody in a project, or change what they are in it (upsert).

        A role-only update keeps an existing display email; giving an email sets
        it. `email` is never read by a decision, only shown.
        """
        existing = await ProjectMember.objects.filter(
            project=project, subject=subject
        ).afirst()
        if existing is None:
            member = await ProjectMember.objects.acreate(
                project=project,
                subject=subject,
                email=email or "",
                role=role,
            )
        else:
            existing.role = role
            if email:
                existing.email = email
            member = await existing.asave()
        return {"subject": member.subject, "email": member.email, "role": member.role}

    async def owner_count(self, project: Project) -> int:
        return await ProjectMember.objects.filter(
            project=project, role=MembershipRole.Owner
        ).acount()

    async def is_owner(self, project: Project, subject: str) -> bool:
        return (
            await ProjectMember.objects.filter(
                project=project, subject=subject, role=MembershipRole.Owner
            ).aexists()
        )

    async def remove_member(self, project: Project, subject: str) -> bool:
        deleted, _ = await ProjectMember.objects.filter(
            project=project, subject=subject
        ).adelete()
        return deleted > 0

    async def create_owner(self, project: Project, subject: str, email: str | None = None) -> None:
        """The person who starts a project owns it. One transaction upstream
        (project + owner) so a project with no members never exists, even briefly."""
        await ProjectMember.objects.acreate(
            project=project,
            subject=subject,
            email=email or "",
            role=MembershipRole.Owner,
        )

    async def bootstrap_owners(self, subjects: list[str]) -> int:
        """Make these people owners of every project that has nobody in it.

        The projects that predate membership have no members, and a project
        nobody is in is a project nobody can open. Only those: a project that
        has members is already answered for, so running this again changes
        nothing.
        """
        memberless = [
            project
            async for project in Project.objects.filter(members__isnull=True).aiterator()
        ]
        inserted = 0
        for subject in subjects:
            for project in memberless:
                _, created = await ProjectMember.objects.aupdate_or_create(
                    project_id=project.id,   # FK is the int PK, not the uuid pid
                    subject=subject,
                    defaults={"role": MembershipRole.Owner},
                )
                inserted += int(created)
        return inserted
