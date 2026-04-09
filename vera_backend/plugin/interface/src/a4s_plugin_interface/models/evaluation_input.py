import enum

from pydantic import BaseModel

class InputType(str, enum.Enum):
    MODEL = "model"
    DATASET = "dataset"


class InputDefinition(BaseModel):
    name: str
    label: str
    input_type: InputType
    required: bool = True