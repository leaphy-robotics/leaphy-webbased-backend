"""FastAPI models"""

from typing import Annotated

from pydantic import BaseModel, Field

# Regex match to (hopefully) prevent weird CLI injection issues
Library = Annotated[str, Field(pattern=r"^[a-zA-Z0-9_ \.@]*$")]


class Sketch(BaseModel):
    """Model representing a arduino Sketch"""

    source_code: str
    board: str
    libraries: list[Library] = []


class PythonProgram(BaseModel):
    """Model representing a python program"""

    source_code: bytes  # Base64 encoded program
    filename: str = ""


class Message(BaseModel):
    """Model representing a message"""

    role: str
    content: str


class Messages(BaseModel):
    """Model representing a collection of messages"""

    messages: list[Message]
