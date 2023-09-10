""" FastAPI models """
from typing import Annotated

from pydantic import BaseModel, Field

# Regex match to (hopefully) prevent weird CLI injection issues
Library = Annotated[str, Field(pattern=r"^[a-zA-Z0-9_ ]*$")]


class Sketch(BaseModel):
    """Model representing a arduino Sketch"""

    source_code: str
    board: str
    libraries: list[Library] = []
