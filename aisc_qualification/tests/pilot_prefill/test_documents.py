"""Turning an uploaded file into text.

Whatever somebody hands you: a PDF export of the technical documentation, a
Word document, a text or markdown file. Nothing here needs a model, and none of
it should throw at the caller: a file that cannot be read is an empty document
with a reason, because the form still has to open.
"""
import io

import pytest

from aisc_qualification.prefill.documents import DocumentUnreadable, read_document


def test_plain_text():
    assert read_document(b"System name: MCAS\n", "notes.txt") == "System name: MCAS"


def test_markdown_is_text():
    assert "MCAS" in read_document(b"# MCAS\n\nA scoring system.\n", "readme.md")


def test_the_name_decides_and_the_case_of_it_does_not():
    assert read_document(b"hello", "NOTES.TXT") == "hello"


def test_text_that_is_not_utf8_is_still_read():
    """A Word export saved as text is often cp1252. Losing a curly quote is
    better than losing the document."""
    text = read_document("System: café".encode("cp1252"), "notes.txt")
    assert "System" in text


def test_an_unknown_extension_is_refused_rather_than_guessed():
    with pytest.raises(DocumentUnreadable) as refused:
        read_document(b"\x00\x01", "system.exe")
    assert "exe" in str(refused.value)


def test_an_empty_file_is_refused():
    with pytest.raises(DocumentUnreadable):
        read_document(b"", "notes.txt")


def test_a_docx_is_read_as_its_paragraphs():
    docx = pytest.importorskip("docx")
    document = docx.Document()
    document.add_heading("System name", level=2)
    document.add_paragraph("MCAS")
    buffer = io.BytesIO()
    document.save(buffer)

    text = read_document(buffer.getvalue(), "technical-documentation.docx")

    assert "System name" in text
    assert "MCAS" in text


def test_a_docx_that_is_not_a_docx_is_refused_not_crashed():
    with pytest.raises(DocumentUnreadable):
        read_document(b"not a zip file at all", "broken.docx")


def test_a_pdf_that_is_not_a_pdf_is_refused_not_crashed():
    with pytest.raises(DocumentUnreadable):
        read_document(b"not a pdf", "broken.pdf")


# A .docx may expand to at most 50 MiB.

from docx_helpers import EXPANDS, docx_bomb, small_docx  # noqa: E402


def _check():
    try:
        from aisc_qualification.prefill.documents import check_docx_expansion
    except ImportError as exc:
        raise AssertionError(f"prefill.documents.check_docx_expansion is missing: {exc}")
    return check_docx_expansion


def test_check_docx_expansion_passes_a_normal_docx(monkeypatch):
    monkeypatch.delenv("PREFILL_MAX_UNZIPPED_BYTES", raising=False)
    assert _check()(small_docx()) is None


def test_check_docx_expansion_refuses_more_than_the_limit(monkeypatch):
    monkeypatch.delenv("PREFILL_MAX_UNZIPPED_BYTES", raising=False)
    with pytest.raises(DocumentUnreadable) as refused:
        _check()(docx_bomb())
    assert str(refused.value) == EXPANDS.format(52428800)


def test_check_docx_expansion_turns_a_bad_zip_into_the_existing_message():
    with pytest.raises(DocumentUnreadable) as refused:
        _check()(b"not a zip at all")
    assert str(refused.value).startswith("this .docx could not be read: ")


def test_exactly_the_limit_is_still_fine(monkeypatch):
    import zipfile

    raw = small_docx()
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        total = sum(i.file_size for i in z.infolist())
    monkeypatch.setenv("PREFILL_MAX_UNZIPPED_BYTES", str(total))
    assert _check()(raw) is None
    monkeypatch.setenv("PREFILL_MAX_UNZIPPED_BYTES", str(total - 1))
    with pytest.raises(DocumentUnreadable) as refused:
        _check()(raw)
    assert str(refused.value) == EXPANDS.format(total - 1)


def test_the_prefill_reader_refuses_the_bomb_before_docx_reads_it(monkeypatch):
    import docx

    def never(*_a, **_k):
        raise AssertionError("docx.Document was called")

    monkeypatch.delenv("PREFILL_MAX_UNZIPPED_BYTES", raising=False)
    bomb = docx_bomb()
    monkeypatch.setattr(docx, "Document", never)
    with pytest.raises(DocumentUnreadable) as refused:
        read_document(bomb, "technical-documentation.docx")
    assert str(refused.value) == EXPANDS.format(52428800)


def test_the_prefill_reader_uses_the_environment_limit(monkeypatch):
    monkeypatch.setenv("PREFILL_MAX_UNZIPPED_BYTES", "1000")
    with pytest.raises(DocumentUnreadable) as refused:
        read_document(small_docx(), "doc.docx")
    assert "more than 1000 bytes when unpacked; " in str(refused.value)
