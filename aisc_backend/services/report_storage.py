"""Report files use the same object-storage repository as evaluation artifacts."""

import logging
import struct
import uuid
import zlib

from django.conf import settings
from ninja.errors import HttpError

from aisc_backend.models.common import StorageContainer
from aisc_backend.models.report_storage import ReportArtifact
from aisc_backend.repositories import file_repository

logger = logging.getLogger(__name__)


def upload(report, file):
    if file.size > 2_000_000:
        raise HttpError(413, "Chart exceeds 2 MB")
    try:
        raw = file.read()
        if raw[:8] != b"\x89PNG\r\n\x1a\n" or len(raw) < 33:
            raise ValueError("Invalid PNG")
        width, height = struct.unpack(">II", raw[16:24])
        if not width or not height or width * height > 25_000_000:
            raise ValueError("Invalid dimensions")
        offset = 8
        ended = False
        while offset + 12 <= len(raw):
            size = struct.unpack(">I", raw[offset : offset + 4])[0]
            chunk = raw[offset + 4 : offset + 8 + size]
            crc = raw[offset + 8 + size : offset + 12 + size]
            if len(crc) != 4 or zlib.crc32(chunk) != struct.unpack(">I", crc)[0]:
                raise ValueError("Invalid PNG chunk")
            offset += size + 12
            if chunk[:4] == b"IEND":
                ended = True
                break
        if not ended:
            raise ValueError("Incomplete PNG")
    except Exception as exc:
        raise HttpError(422, "A valid PNG chart is required") from exc
    file.seek(0)
    key = f"reports/{report.pid}/{uuid.uuid4()}.png"
    if not file_repository.upload_file(file, StorageContainer.Artifacts, key):
        raise HttpError(502, "Chart upload failed")
    try:
        return ReportArtifact.objects.create(
            report=report,
            name="chart.png",
            description="Report chart",
            data=key,
            file_size=file.size,
        )
    except Exception:
        try:
            if not file_repository.delete_object(StorageContainer.Artifacts, key):
                logger.error("Failed to clean up report artifact %s", key)
        except Exception:
            logger.exception("Failed to clean up report artifact %s", key)
        raise



def store_images(report, snapshot):
    """Validate every chart reference against the report's own artifacts.

    Snapshot chart slots carry ``artifact:<uuid>`` references; a reference to
    anything but a real artifact of this exact report is rejected. Image bytes
    are no longer accepted here — they arrive as separate PNG uploads."""

    def visit(value):
        if isinstance(value, list):
            for child in value:
                visit(child)
        elif isinstance(value, dict):
            image = value.get("image", "")
            if isinstance(image, str) and image.startswith("artifact:"):
                try:
                    artifact_pid = uuid.UUID(image[9:])
                except ValueError as exc:
                    raise HttpError(404, "No such report artifact") from exc
                if not ReportArtifact.objects.filter(report=report, pid=artifact_pid).exists():
                    raise HttpError(404, "No such report artifact")
                value["image"] = image
            elif image:
                raise HttpError(422, "Unsupported chart image")
            for child in value.values():
                if isinstance(child, (dict, list)):
                    visit(child)

    visit(snapshot)
    return snapshot


def for_renderer(snapshot, project_pid):
    origin = getattr(settings, "REPORT_BACKEND_INTERNAL_URL", "http://aisc-backend:8000").rstrip(
        "/"
    )

    def visit(value):
        if isinstance(value, list):
            for child in value:
                visit(child)
        elif isinstance(value, dict):
            image = value.get("image", "")
            if isinstance(image, str) and image.startswith("artifact:"):
                value["image"] = (
                    f"{origin}/api/v1/projects/{project_pid}/report-artifacts/{image[9:]}"
                )
            for child in value.values():
                if isinstance(child, (dict, list)):
                    visit(child)

    from copy import deepcopy

    result = deepcopy(snapshot)
    visit(result)
    return result


def delete_files(report):
    """Delete every artifact's stored object.

    A missing object counts as already deleted; every artifact is attempted
    even if one deletion fails or the storage call raises (a connection error
    must not stop the rest). Metadata is removed only for artifacts whose
    object was deleted; failed artifacts (and the report row) are retained so
    a later cleanup run can retry them. Raises an aggregate error listing the
    failures after attempting all files.
    """

    def delete_one(artifact):
        try:
            deleted = file_repository.delete_object(
                artifact.storage_container, artifact.data
            )
        except Exception:  # noqa: BLE001 - one bad call must not stop the rest
            deleted = False
        if deleted:
            artifact.delete()
            return None
        return artifact.data

    failures = [failed for failed in (delete_one(a) for a in report.artifacts.all()) if failed]
    if failures:
        raise RuntimeError(f"Failed to delete report artifacts: {', '.join(failures)}")
