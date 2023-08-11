from typing import Annotated

from pydantic import BaseModel, Field

# Regex match to (hopefully) prevent weird CLI injection issues
Library = Annotated[str, Field(pattern=r"^[a-zA-Z0-9_ ]*$")]


class Sketch(BaseModel):
    source_code: str
    # TODO: make this an enum with supported board types
    board: str
    libraries: list[Library] = []
