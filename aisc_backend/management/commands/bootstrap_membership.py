"""
Make the configured subjects owner of every project that has nobody in it.

The projects that predate membership have no members, and a project nobody is
in is a project nobody can open. This is the single-tenant backfill: read the
comma-separated subjects from PROJECT_BOOTSTRAP_OWNERS and make each one owner
of every memberless project. Idempotent — a project that already has members is
untouched, so running it again changes nothing.

    uv run python manage.py bootstrap_membership
"""
import asyncio

from django.core.management.base import BaseCommand

from aisc_backend.repositories.membership_repository import MembershipRepository
from config.settings import PROJECT_BOOTSTRAP_OWNERS


class Command(BaseCommand):
    help = (
        "Make PROJECT_BOOTSTRAP_OWNERS subjects owners of every project that "
        "has no members yet (idempotent)."
    )

    def handle(self, *args, **options):
        subjects = [s.strip() for s in PROJECT_BOOTSTRAP_OWNERS.split(",") if s.strip()]
        if not subjects:
            self.stdout.write("PROJECT_BOOTSTRAP_OWNERS is empty; nothing to do.")
            return
        inserted = asyncio.run(MembershipRepository().bootstrap_owners(subjects))
        self.stdout.write(f"Made {len(subjects)} subject(s) owner of {inserted} project membership row(s).")
