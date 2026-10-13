"""Command line interface: ``sqltrace analyze`` and ``sqltrace explain``."""

from __future__ import annotations

import argparse
import json
import sys
from typing import List, Optional

from . import __version__
from .analyzer import AnalysisResult, analyze
from .errors import SQLTraceError, SQLSyntaxError
from .report import render_diagnostic, render_explanation, render_text
from .schema import Schema

EXIT_OK, EXIT_DIAGNOSTICS, EXIT_INPUT = 0, 1, 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="sqltrace", description="Trace where SQL output columns come from.")
    parser.add_argument("--version", action="version", version=f"sqltrace {__version__}")
    commands = parser.add_subparsers(dest="command", required=True)

    def common(sub: argparse.ArgumentParser) -> None:
        sub.add_argument("query", nargs="?", help="SQL text (or use --file)")
        sub.add_argument("-f", "--file", help="read the query from a file ('-' for stdin)")
        sub.add_argument("-s", "--schema", help="JSON file: {\"table\": [\"col\", ...]}")
        sub.add_argument("-d", "--dialect", help="SQL dialect (postgres, mysql, ...)")

    analyze_cmd = commands.add_parser("analyze", help="list output columns and their sources")
    common(analyze_cmd)
    analyze_cmd.add_argument(
        "--format", choices=["text", "json", "dot", "mermaid"], default="text", help="output format"
    )

    explain_cmd = commands.add_parser("explain", help="show how one output column is derived")
    common(explain_cmd)
    explain_cmd.add_argument("-c", "--column", required=True, help="output column name")
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    sql = ""
    try:
        sql = _read_sql(args)
        schema = Schema.from_file(args.schema) if args.schema else None
        result = analyze(sql, schema=schema, dialect=args.dialect)
    except SQLTraceError as error:
        severity = "error"
        print(render_diagnostic(error.code, severity, error.message, error.position, sql), file=sys.stderr)
        return EXIT_INPUT
    except OSError as error:
        print(f"error: cannot read '{error.filename}': {error.strerror}", file=sys.stderr)
        return EXIT_INPUT

    if args.command == "explain":
        return _explain(result, args.column)
    print(_format(result, args.format))
    return EXIT_OK if result.ok else EXIT_DIAGNOSTICS


def _read_sql(args: argparse.Namespace) -> str:
    if args.file and args.query:
        raise SQLSyntaxError("Give either a query or --file, not both.")
    if args.file == "-":
        return sys.stdin.read()
    if args.file:
        with open(args.file, encoding="utf-8") as handle:
            return handle.read()
    if args.query is None:
        raise SQLSyntaxError("No query given: pass SQL text or --file.")
    return args.query


def _format(result: AnalysisResult, fmt: str) -> str:
    if fmt == "json":
        return json.dumps(result.to_dict(), indent=2, ensure_ascii=False)
    if fmt == "dot":
        return result.graph.to_dot()
    if fmt == "mermaid":
        return result.graph.to_mermaid()
    return render_text(result)


def _explain(result: AnalysisResult, column: str) -> int:
    try:
        print(render_explanation(result, column))
    except KeyError:
        names = ", ".join(o.name for o in result.outputs) or "none"
        print(f"error: no output column named '{column}' (available: {names})", file=sys.stderr)
        return EXIT_INPUT
    return EXIT_OK if result.ok else EXIT_DIAGNOSTICS


if __name__ == "__main__":
    raise SystemExit(main())
