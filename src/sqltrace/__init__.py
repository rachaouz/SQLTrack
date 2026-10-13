"""SQLTrace: column-level lineage for SQL queries."""

from .analyzer import AnalysisResult, OutputColumn, analyze
from .errors import (
    Diagnostic,
    Position,
    SchemaError,
    Severity,
    SQLSyntaxError,
    SQLTraceError,
    UnsupportedSQLError,
)
from .graph import LineageGraph, Node, NodeKind
from .schema import Schema

__version__ = "1.0.0"

__all__ = [
    "analyze",
    "AnalysisResult",
    "OutputColumn",
    "Schema",
    "LineageGraph",
    "Node",
    "NodeKind",
    "Diagnostic",
    "Severity",
    "Position",
    "SQLTraceError",
    "SQLSyntaxError",
    "UnsupportedSQLError",
    "SchemaError",
    "__version__",
]
