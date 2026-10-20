import csv
import zipfile

import io

def csv_bytes_to_rows(csv_bytes: bytes, row_limit: int = 100) -> list[dict]:
    decoded_content = csv_bytes.decode('utf-8')
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