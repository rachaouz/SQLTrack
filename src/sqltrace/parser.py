"""SQL text -> SQLGlot AST. All parsing is delegated to SQLGlot."""

from __future__ import annotations

from typing import Optional

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError, TokenError

from .errors import Position, SQLSyntaxError, UnsupportedSQLError


def parse_sql(sql: str, dialect: Optional[str] = None) -> exp.Expression:
    """Parse ``sql`` into a single query expression.

    Raises:
        SQLSyntaxError: the text is empty or not valid SQL for ``dialect``.
        UnsupportedSQLError: several statements, or a statement that is not a query.
    """
    if not sql or not sql.strip():
        raise SQLSyntaxError("The query is empty.")
    try:
        statements = [s for s in sqlglot.parse(sql, read=dialect) if s is not None]
    except ParseError as error:
        raise _syntax_error(sql, error) from error
    except TokenError as error:
        raise SQLSyntaxError(str(error).splitlines()[0]) from error
    except ValueError as error:  # unknown dialect name
        raise SQLSyntaxError(str(error)) from error

    if not statements:
        raise SQLSyntaxError("The query is empty.")
    if len(statements) > 1:
        raise UnsupportedSQLError("Only one statement can be analysed at a time.")
    statement = statements[0]
    if not isinstance(statement, exp.Query):
        raise UnsupportedSQLError(
            f"Only SELECT queries are supported (got {type(statement).__name__})."
        )
    return statement


def _syntax_error(sql: str, error: ParseError) -> SQLSyntaxError:
    details = error.errors[0] if error.errors else {}
    message = str(details.get("description") or str(error).splitlines()[0])
    highlight = str(details.get("highlight") or "")
    # SQLGlot reports a token as an error with its (1-based) *end* column.
    position: Optional[Position] = None
    line, col = details.get("line"), details.get("col")
    if line and col:
        length = len(highlight) or 1
        lines = sql.split("\n")
        offset = sum(len(text) + 1 for text in lines[: line - 1])
        start = offset + max(col - length, 0)
        position = Position.from_offsets(sql, start, start + length)
    # SQLGlot's descriptions embed a raw token repr: keep only the readable part.
    if "Token token_type" in message:
        message = message.split(" but got ")[0]
        if highlight:
            message += f" but got '{highlight}'"
    return SQLSyntaxError(message, position)
