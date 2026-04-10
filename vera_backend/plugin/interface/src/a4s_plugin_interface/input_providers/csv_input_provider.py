import csv
import io

from .base_input_provider import BaseInputProvider


class CsvInputProvider(BaseInputProvider):
    """
    A concrete implementation of BaseInputProvider for CSV files.
    Parses the file content into a list of dictionaries, where each dict represents a row.
    """

    def _read_data(self, file_content) -> list[dict]:
        """
        Converts CSV bytes into a list of dictionaries.
        """
        file_stream = io.BytesIO(file_content)
        wrapper = io.TextIOWrapper(file_stream, encoding='utf-8')
        reader = csv.DictReader(wrapper)
        return list(reader)
