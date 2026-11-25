from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db.models import Q
from django.utils import timezone

from aisc_backend.models.report import Report
from aisc_backend.services.report_storage import delete_files


class Command(BaseCommand):
    help = (
        "Remove abandoned report preparations older than 24 hours and pending "
        "reports whose generation started more than 24 hours ago (existing rows "
        "without a generation-start timestamp fall back to their creation time). "
        "A failed cleanup keeps the report row so the next run can retry it."
    )

    def handle(self, **options):
        cutoff = timezone.now() - timedelta(hours=24)
        abandoned = Report.objects.filter(
            Q(status="preparing", created_at__lt=cutoff)
            | Q(status="pending")
            & (Q(generation_started_at__isnull=True, created_at__lt=cutoff)
               | Q(generation_started_at__lt=cutoff))
        )
        failures = 0
        for report in abandoned:
            try:
                delete_files(report)
                report.delete()
            except Exception as exc:  # noqa: BLE001 - one bad report must not stop the others
                failures += 1
                self.stderr.write(f"report {report.pid}: cleanup failed: {exc}")
        if failures:
            self.stderr.write(
                f"{failures} abandoned report(s) kept for a retry on the next run"
            )
