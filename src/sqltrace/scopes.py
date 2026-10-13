"""Query scopes: which sources are visible in a SELECT and how a column name resolves."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Set, Tuple


class SourceKind(str, Enum):
    TABLE = "table"
    CTE = "cte"
    SUBQUERY = "subquery"
    UNSUPPORTED = "unsupported"


class ResolutionStatus(str, Enum):
    FOUND = "found"
    MISSING = "missing"  # certain: no visible source has this column
    AMBIGUOUS = "ambiguous"  # certain: several sources have it
    UNRESOLVABLE = "unresolvable"  # undecidable: some source has unknown columns
    UNKNOWN_QUALIFIER = "unknown_qualifier"  # certain: no source with that name/alias


@dataclass
class Source:
    """One item of a FROM/JOIN clause as seen by the query that uses it.

    ``columns`` maps a lowercase column name to the graph node id that provides
    it, or is ``None`` when the columns of this source are unknown.
    """

    name: str
    kind: SourceKind
    node_prefix: str
    columns: Optional[Dict[str, str]] = None

    @property
    def has_known_columns(self) -> bool:
        return self.columns is not None

    def has_column(self, column: str) -> bool:
        return self.columns is not None and column in self.columns

    def node_for(self, column: str) -> str:
        if self.columns is not None and column in self.columns:
            return self.columns[column]
        return f"{self.node_prefix}.{column}"

    def available(self) -> List[str]:
        return list(self.columns) if self.columns is not None else []


@dataclass
class Resolution:
    status: ResolutionStatus
    found: List[Tuple[Source, str]] = field(default_factory=list)
    candidates: List[str] = field(default_factory=list)
    available: List[str] = field(default_factory=list)


@dataclass(eq=False)
class Scope:
    """The sources of one SELECT, chained to the enclosing scope (correlated subqueries)."""

    parent: Optional["Scope"] = None
    sources: Dict[str, Source] = field(default_factory=dict)
    using: Set[str] = field(default_factory=set)  # columns merged by JOIN ... USING
    ctes: Dict[str, Any] = field(default_factory=dict)

    def add_source(self, source: Source) -> bool:
        """Register ``source``; return ``False`` if its name is already taken."""
        if source.name in self.sources:
            return False
        self.sources[source.name] = source
        return True

    def resolve(self, column: str, qualifier: Optional[str] = None) -> Resolution:
        """Resolve a (lowercase) column reference, innermost scope first."""
        if qualifier is not None:
            return self._resolve_qualified(column, qualifier)
        scope: Optional[Scope] = self
        while scope is not None:
            result = scope._resolve_unqualified(column)
            if result.status is not ResolutionStatus.MISSING:
                return result
            scope = scope.parent
        return Resolution(ResolutionStatus.MISSING, available=self._all_columns())

    def _resolve_qualified(self, column: str, qualifier: str) -> Resolution:
        scope: Optional[Scope] = self
        while scope is not None:
            source = scope.sources.get(qualifier)
            if source is not None:
                if source.has_column(column):
                    return Resolution(ResolutionStatus.FOUND, found=[(source, source.node_for(column))])
                if source.has_known_columns:
                    return Resolution(ResolutionStatus.MISSING, available=source.available())
                # Columns unknown: the qualifier makes the origin certain.
                return Resolution(ResolutionStatus.FOUND, found=[(source, source.node_for(column))])
            scope = scope.parent
        return Resolution(ResolutionStatus.UNKNOWN_QUALIFIER, available=list(self.sources))

    def _resolve_unqualified(self, column: str) -> Resolution:
        defining = [s for s in self.sources.values() if s.has_column(column)]
        unknown = [s for s in self.sources.values() if not s.has_known_columns]
        if column in self.using and len(defining) >= 2:
            return Resolution(ResolutionStatus.FOUND, found=[(s, s.node_for(column)) for s in defining])
        if len(defining) >= 2:
            return Resolution(ResolutionStatus.AMBIGUOUS, candidates=[s.name for s in defining])
        if len(defining) == 1:
            if unknown:
                names = [defining[0].name] + [s.name for s in unknown]
                return Resolution(ResolutionStatus.UNRESOLVABLE, candidates=names)
            source = defining[0]
            return Resolution(ResolutionStatus.FOUND, found=[(source, source.node_for(column))])
        if len(unknown) == 1:
            # Every other source is known not to have it, so it can only be this one.
            source = unknown[0]
            return Resolution(ResolutionStatus.FOUND, found=[(source, source.node_for(column))])
        if len(unknown) >= 2:
            return Resolution(ResolutionStatus.UNRESOLVABLE, candidates=[s.name for s in unknown])
        return Resolution(ResolutionStatus.MISSING, available=self._all_columns())

    def _all_columns(self) -> List[str]:
        names: List[str] = []
        for source in self.sources.values():
            for column in source.available():
                if column not in names:
                    names.append(column)
        return names
