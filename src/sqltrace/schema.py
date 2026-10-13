"""User-provided schema: which columns each table has."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List, Mapping, Optional, Union

from .errors import SchemaError

SchemaInput = Union["Schema", Mapping[str, Union[List[str], Mapping[str, object]]]]


class Schema:
    """Case-insensitive mapping ``table name -> ordered column names``.

    Table names may be qualified (``"sales.orders"``). A table written without
    qualifier in a query matches a schema entry only if the match is unique.
    """

    def __init__(self, tables: Mapping[str, Union[List[str], Mapping[str, object]]]) -> None:
        self._tables: Dict[str, List[str]] = {}
        if not isinstance(tables, Mapping):
            raise SchemaError("A schema must be a mapping of table name -> columns.")
        for table, columns in tables.items():
            if not isinstance(table, str) or not table.strip():
                raise SchemaError("Table names in a schema must be non-empty strings.")
            if isinstance(columns, Mapping):
                names = list(columns.keys())  # {"col": "TYPE"} form: types are ignored
            elif isinstance(columns, (list, tuple)):
                names = list(columns)
            else:
                raise SchemaError(f"Columns of table '{table}' must be a list or a mapping.")
            if not all(isinstance(name, str) and name.strip() for name in names):
                raise SchemaError(f"Column names of table '{table}' must be non-empty strings.")
            self._tables[table.lower()] = [name.lower() for name in names]

    @classmethod
    def from_file(cls, path: Union[str, Path]) -> "Schema":
        """Load a schema from a JSON file."""
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
        except OSError as error:
            raise SchemaError(f"Cannot read schema file '{path}': {error.strerror}") from error
        except json.JSONDecodeError as error:
            raise SchemaError(f"Schema file '{path}' is not valid JSON: {error}") from error
        return cls(data)

    @classmethod
    def coerce(cls, value: Optional[SchemaInput]) -> Optional["Schema"]:
        if value is None or isinstance(value, Schema):
            return value
        return cls(value)

    def find(self, table: str) -> Optional[str]:
        """Return the canonical schema key for ``table`` or ``None`` if unknown/ambiguous."""
        key = table.lower()
        if key in self._tables:
            return key
        if "." not in key:
            matches = [name for name in self._tables if name.endswith("." + key)]
            if len(matches) == 1:
                return matches[0]
        return None

    def columns(self, table: str) -> Optional[List[str]]:
        key = self.find(table)
        return list(self._tables[key]) if key else None

    def tables(self) -> List[str]:
        return list(self._tables)
