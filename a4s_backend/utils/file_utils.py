from django.core.files import File
import pandas as pd
import io


def csv_to_parquet(file: File):
    # Read CSV content into pandas
    contents = file.read()
    df = pd.read_csv(io.BytesIO(contents))

    # Convert to parquet bytes
    parquet_buffer = io.BytesIO()
    df.to_parquet(parquet_buffer)
    parquet_buffer.seek(0)

    # Replace original file with parquet version
    file.file = parquet_buffer
    file.name = "temp.parquet"

    file.seek(0)