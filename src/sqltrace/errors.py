"""Exceptions and diagnostics shared by every SQLTrace module."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, Optional


@dataclass(frozen=True)
class Position:
    """A span in the original SQL text (1-based line/column, 0-based offsets)."""

    line: int
    column: int
    start: int
    end: int  # exclusive

    @classmethod
    def from_offsets(cls, sql: str, start: int, end: int) -> "Position":
        """Build a position from character offsets into ``sql``."""
        start = max(0, min(start, len(sql)))
        end = max(start, min(end, len(sql)))
        line = sql.count("\n", 0, start) + 1
        column = start - sql.rfind("\n", 0, start)
        return cls(line=line, column=column, start=start, end=end)

    def to_dict(self) -> Dict[str, int]:
        return {"line": self.line, "column": self.column, "start": self.start, "end": self.end}


class SQLTraceError(Exception):
    """Base class for errors that stop an analysis."""

    code = "SQT000"

    def __init__(self, message: str, position: Optional[Position] = None) -> None:
        super().__init__(message)
        self.message = message
        self.position = position

    def __str__(self) -> str:
        if self.position is None:
            return self.message
        return f"{self.message} (line {self.position.line}, column {self.position.column})"


class SQLSyntaxError(SQLTraceError):
    """The SQL text could not be parsed."""

    code = "SQT001"


class UnsupportedSQLError(SQLTraceError):
    """The SQL is valid but uses a construct SQLTrace does not handle."""

    code = "SQT002"


class SchemaError(SQLTraceError):
    """The user-provided schema is malformed."""

    code = "SQT003"


class Severity(str, Enum):
    """``ERROR``: certain problem. ``WARNING``: cannot be decided with the information available."""

    ERROR = "error"
    WARNING = "warning"


@dataclass(frozen=True)
class Diagnostic:
    """A problem found while analysing a query (the analysis still completes)."""

    code: str
    severity: Severity
    message: str
    position: Optional[Position] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "code": self.code,
            "severity": self.severity.value,
            "message": self.message,
            "position": self.position.to_dict() if self.position else None,
        }
