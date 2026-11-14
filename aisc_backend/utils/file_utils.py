import base64
import csv
import zipfile

import io

#: how much of an artifact the run page reads to preview it
PREVIEW_BYTES = 256 * 1024


def csv_bytes_to_rows(csv_bytes: bytes, row_limit: int = 100) -> list[dict]:
    decoded_content = csv_bytes.decode('utf-8', errors='replace')
    csv_reader = csv.reader(io.StringIO(decoded_content))

    rows = []
    for i, row in enumerate(csv_reader):
        if i >= row_limit:
            break
        rows.append(row)

    return rows


def zip_bytes_to_file_list(zip_bytes: bytes) -> list[dict]:
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as z:
        file_list = []

        for info in z.infolist():
            file_list.append({
                "file_name": info.filename,
                "file_size": info.file_size,
            })

        return file_list

def preview_of(suffix: str, head: bytes, truncated: bool) -> object | None:
    """What the run page shows of an artifact, from the first PREVIEW_BYTES of it (`truncated` when the file
    goes on). Text is decoded with replacement characters; a csv shows its first 100 whole rows; a png, a pdf
    or a zip only when the whole file was read. Never raises: what cannot be previewed gives None."""
    try:
        if suffix == ".csv":
            if truncated and b"\n" in head:
                head = head[:head.rindex(b"\n") + 1]  # the last row may be cut
            return csv_bytes_to_rows(head)
        if suffix in (".log", ".txt"):
            return head.decode("utf-8", errors="replace")
        if truncated:
            return None
        if suffix == ".png":
            return f"data:image/png;base64,{base64.b64encode(head).decode('ascii')}"
        if suffix == ".pdf":
            return f"data:application/pdf;base64,{base64.b64encode(head).decode('ascii')}"
        if suffix == ".zip":
            return zip_bytes_to_file_list(head)
    except Exception:
        return None
    return None
