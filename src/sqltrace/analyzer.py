"""Public API: ``analyze(sql, schema=None, dialect=None) -> AnalysisResult``."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .errors import Diagnostic, Severity
from .graph import LineageGraph
from .parser import parse_sql
from .resolver import Resolver
from .schema import Schema, SchemaInput


@dataclass(frozen=True)
class OutputColumn:
    """An output column of the query and where it comes from."""

    name: str
    node_id: str
    expression: str
    kind: str  # "column" | "expression" | "constant"
    direct_sources: List[str]  # what it is computed from, one level down
    base_sources: List[str]  # real-table columns it transitively depends on
    constants: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "node_id": self.node_id,
            "expression": self.expression,
            "kind": self.kind,
            "direct_sources": self.direct_sources,
            "base_sources": self.base_sources,
            "constants": self.constants,
        }


@dataclass
class AnalysisResult:
    sql: str
    outputs: List[OutputColumn]
    graph: LineageGraph
    diagnostics: List[Diagnostic]

    @property
    def errors(self) -> List[Diagnostic]:
        return [d for d in self.diagnostics if d.severity is Severity.ERROR]

    @property
    def warnings(self) -> List[Diagnostic]:
        return [d for d in self.diagnostics if d.severity is Severity.WARNING]

    @property
    def ok(self) -> bool:
        """``True`` when no certain error was found (warnings are allowed)."""
        return not self.errors

    def output(self, name: str) -> OutputColumn:
        """Find an output column by name (case-insensitive)."""
        for column in self.outputs:
            if column.name.lower() == name.lower():
                return column
        raise KeyError(name)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "sql": self.sql,
            "ok": self.ok,
            "outputs": [o.to_dict() for o in self.outputs],
            "diagnostics": [d.to_dict() for d in self.diagnostics],
            "graph": self.graph.to_dict(),
        }


def analyze(sql: str, schema: Optional[SchemaInput] = None, dialect: Optional[str] = None) -> AnalysisResult:
    """Trace the origin of every output column of ``sql``.

    Args:
        sql: a single SELECT / WITH / UNION query.
        schema: optional ``{"table": ["col", ...]}`` mapping (or a :class:`Schema`).
        dialect: SQLGlot dialect name (``"postgres"``, ``"mysql"``...), default generic SQL.

    Raises:
        SQLSyntaxError, UnsupportedSQLError, SchemaError: the analysis cannot start or continue.
    """
    resolved_schema = Schema.coerce(schema)
    statement = parse_sql(sql, dialect)
    resolver = Resolver(sql, resolved_schema)
    raw_outputs = resolver.resolve(statement)
    graph = resolver.graph
    outputs = [
        OutputColumn(
            name=o.name,
            node_id=o.node_id,
            expression=o.expression,
            kind=o.kind,
            direct_sources=graph.direct_sources(o.node_id),
            base_sources=graph.base_sources(o.node_id),
            constants=o.constants,
        )
        for o in raw_outputs
    ]
    diagnostics = sorted(
        resolver.diagnostics, key=lambda d: (d.position.start if d.position else -1, d.code)
    )
    return AnalysisResult(sql, outputs, graph, diagnostics)
