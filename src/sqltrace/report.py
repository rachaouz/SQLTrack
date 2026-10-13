"""Human-readable rendering of analysis results and diagnostics."""

from __future__ import annotations

from typing import List, Optional

from .analyzer import AnalysisResult
from .errors import Diagnostic, Position


def render_diagnostic(
    code: str, severity: str, message: str, position: Optional[Position], sql: str
) -> str:
    """One diagnostic, with the offending line and a caret marker when the position is known."""
    head = f"{severity}[{code}]"
    if position is not None:
        head += f" line {position.line}, column {position.column}"
    lines = [f"{head}: {message}"]
    if position is not None:
        source_lines = sql.split("\n")
        if 0 < position.line <= len(source_lines):
            text = source_lines[position.line - 1]
            width = max(1, min(position.end - position.start, len(text) - position.column + 1))
            lines.append(f"  {text}")
            lines.append("  " + " " * (position.column - 1) + "^" * width)
    return "\n".join(lines)


def render_diagnostics(diagnostics: List[Diagnostic], sql: str) -> str:
    return "\n".join(
        render_diagnostic(d.code, d.severity.value, d.message, d.position, sql) for d in diagnostics
    )


def render_text(result: AnalysisResult) -> str:
    """Summary of output columns, their sources and diagnostics."""
    lines: List[str] = ["Output columns:"]
    if not result.outputs:
        lines.append("  (none could be determined)")
    for column in result.outputs:
        lines.append(f"  {column.name}  [{column.kind}]  {column.expression}")
        if column.base_sources:
            lines.append(f"      from: {', '.join(column.base_sources)}")
        if column.direct_sources and column.direct_sources != column.base_sources:
            lines.append(f"      via:  {', '.join(column.direct_sources)}")
        if column.constants:
            lines.append(f"      constants: {', '.join(column.constants)}")
    if result.diagnostics:
        lines += ["", "Diagnostics:", render_diagnostics(result.diagnostics, result.sql)]
    return "\n".join(lines)


def render_explanation(result: AnalysisResult, column_name: str) -> str:
    """Dependency tree plus the transitive base columns of one output column."""
    column = result.output(column_name)
    lines = [result.graph.explain(column.node_id), ""]
    lines.append("Direct dependencies:     " + (", ".join(column.direct_sources) or "none"))
    lines.append("Base table dependencies: " + (", ".join(column.base_sources) or "none"))
    if column.constants:
        lines.append("Constants:               " + ", ".join(column.constants))
    return "\n".join(lines)
