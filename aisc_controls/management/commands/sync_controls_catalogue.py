"""
Upsert the controls catalogue from the hosted API into the control tables.

Replaces the old vendored seed: the checklist data now lives in the hosted
catalogue (AI Factory Sandbox Configurator); this command pulls it in. Running
it again updates in place and never deletes rows (installed checklists and
their answered questions are kept).

    uv run python manage.py sync_controls_catalogue
"""
import asyncio

from django.core.management.base import BaseCommand, CommandError

from aisc_controls.services.controls_catalogue import (
    CatalogueUnavailable,
    sync_controls_catalogue,
)


class Command(BaseCommand):
    help = (
        "Upsert the controls catalogue from the hosted API into the control "
        "tables (idempotent; never deletes questions)."
    )

    def handle(self, *args, **options):
        try:
            summary = asyncio.run(sync_controls_catalogue())
        except CatalogueUnavailable as exc:
            raise CommandError(f"controls catalogue sync failed: {exc}")
        self.stdout.write(
            "Synced controls catalogue: "
            f"{summary['templates']} templates, "
            f"{summary['questions_created']} questions created, "
            f"{summary['questions_updated']} updated."
        )
        if summary["errors"]:
            self.stderr.write(f"{len(summary['errors'])} checklist(s) failed:")
            for message in summary["errors"]:
                self.stderr.write(f"  {message}")
