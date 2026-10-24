"""Errors with stable codes from sdd.md section 13. Messages never echo scanned content."""

from typing import ClassVar

from pydantic import BaseModel


class Problem(BaseModel):
    """One input problem. index is the tool entry index when the problem is inside an entry."""

    index: int | None = None
    field: str | None = None
    reason: str


class ToolGuardError(Exception):
    code: ClassVar[str] = "internal_error"

    def __init__(self, message: str, problems: list[Problem] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.problems = problems or []


class InvalidInput(ToolGuardError):
    code = "invalid_input"


class PayloadTooLarge(ToolGuardError):
    code = "payload_too_large"


class StorageError(ToolGuardError):
    code = "storage_error"


class InternalError(ToolGuardError):
    code = "internal_error"
