"""The .docx builders the pilot's document tests use, lifted from its own suite.

`services/prefill/tests/test_form_import.py` (pilot branch, `aisc-qualification`)
defines them next to the form-file tests; the document tests import them from
there, so they are kept here instead of pulling the whole form-file suite in.
"""
import io

import pytest


def docx_bomb(total_mib: int = 51) -> bytes:
    """A real .docx plus one highly compressible member, declaring total_mib MiB unpacked."""
    import zipfile

    docx = pytest.importorskip("docx")
    document = docx.Document()
    document.add_paragraph("Who signs off a model release?")
    base = io.BytesIO()
    document.save(base)
    out = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(base.getvalue())) as src, zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as dst:
        for item in src.infolist():
            dst.writestr(item, src.read(item.filename))
        dst.writestr("word/media/zeros.bin", b"\0" * (total_mib * 1024 * 1024))
    return out.getvalue()


def small_docx() -> bytes:
    docx = pytest.importorskip("docx")
    document = docx.Document()
    document.add_paragraph("Who audits?")
    out = io.BytesIO()
    document.save(out)
    return out.getvalue()


EXPANDS = "this .docx expands to more than {} bytes when unpacked; remove embedded media or split it"
