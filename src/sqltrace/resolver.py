"""Column resolution: walks the AST scope by scope and builds the lineage graph."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Iterator, List, Optional

from sqlglot import exp

from .errors import Diagnostic, Position, Severity, UnsupportedSQLError
from .graph import LineageGraph, Node, NodeKind
from .schema import Schema
from .scopes import Resolution, ResolutionStatus, Scope, Source, SourceKind

_CONSTANT_TYPES = (exp.Literal, exp.Null, exp.Boolean)


@dataclass
class Output:
    """One output column of a query, before/after it gets a graph node."""

    name: str
    deps: List[str]  # node ids this column is computed from
    expression: str
    kind: str  # "column" | "expression" | "constant"
    constants: List[str] = field(default_factory=list)
    filter_deps: List[str] = field(default_factory=list)  # WHERE / ON / GROUP BY / HAVING columns
    node_id: str = ""


@dataclass
class QueryResult:
    outputs: List[Output]
    complete: bool = True  # False when a ``*`` could not be expanded
    scope_id: str = ""


class Resolver:
    """Resolves every column reference of one statement and fills ``graph``."""

    def __init__(self, sql: str, schema: Optional[Schema] = None) -> None:
        self.sql = sql
        self.schema = schema
        self.graph = LineageGraph()
        self.diagnostics: List[Diagnostic] = []
        self._used_ids: set = set()

    # -- public entry point --------------------------------------------------
    def resolve(self, statement: exp.Expression) -> List[Output]:
        result = self._resolve_query(statement, {}, None, "query", NodeKind.OUTPUT, True)
        return result.outputs

    # -- queries ---------------------------------------------------------------
    def _resolve_query(
        self,
        node: exp.Expression,
        ctes: Dict[str, QueryResult],
        parent: Optional[Scope],
        scope_id: str,
        kind: NodeKind,
        trace: bool,
        rename: Optional[List[str]] = None,
    ) -> QueryResult:
        """Resolve a query and give each of its outputs a node (``trace`` adds it to the graph)."""
        scope_id = self._unique(scope_id)
        result = self._resolve_raw(node, ctes, parent, trace)
        result.scope_id = scope_id
        if rename:
            for output, new_name in zip(result.outputs, rename):
                output.name = new_name
        taken: set = set()
        for output in result.outputs:
            base = f"{scope_id}.{output.name.lower()}"
            node_id, counter = base, 2
            while node_id in taken:
                node_id, counter = f"{base}#{counter}", counter + 1
            taken.add(node_id)
            output.node_id = node_id
            if trace:
                self.graph.add_node(Node(node_id, kind, output.name, output.expression))
                for dep in output.deps:
                    self.graph.add_edge(dep, node_id)
                for dep in output.filter_deps:
                    self.graph.add_filter_edge(dep, node_id)
        return result

    def _resolve_raw(
        self, node: exp.Expression, ctes: Dict[str, QueryResult], parent: Optional[Scope], trace: bool
    ) -> QueryResult:
        while isinstance(node, exp.Subquery):
            node = node.this
        ctes = self._register_ctes(node, ctes, trace)
        if isinstance(node, exp.Select):
            return self._resolve_select(node, ctes, parent, trace)
        if isinstance(node, exp.SetOperation):
            return self._resolve_set_operation(node, ctes, parent, trace)
        raise UnsupportedSQLError(f"Unsupported query construct: {type(node).__name__}.")

    def _register_ctes(
        self, node: exp.Expression, ctes: Dict[str, QueryResult], trace: bool
    ) -> Dict[str, QueryResult]:
        with_ = node.args.get("with_") or node.args.get("with")
        if not with_:
            return ctes
        if with_.args.get("recursive"):
            raise UnsupportedSQLError("Recursive CTEs (WITH RECURSIVE) are not supported.")
        ctes = dict(ctes)
        for cte in with_.expressions:
            name = cte.alias_or_name.lower()
            alias = cte.args.get("alias")
            renamed = [c.name for c in alias.columns] if alias is not None and alias.columns else None
            ctes[name] = self._resolve_query(
                cte.this, ctes, None, f"cte:{name}", NodeKind.DERIVED, trace, rename=renamed
            )
        return ctes

    def _resolve_set_operation(
        self, node: exp.SetOperation, ctes: Dict[str, QueryResult], parent: Optional[Scope], trace: bool
    ) -> QueryResult:
        """UNION / INTERSECT / EXCEPT: output *i* depends on output *i* of both branches."""
        left = self._resolve_raw(node.this, ctes, parent, trace)
        right = self._resolve_raw(node.expression, ctes, parent, trace)
        complete = left.complete and right.complete
        if complete and len(left.outputs) != len(right.outputs):
            self._report(
                "SQT105", Severity.ERROR, None,
                f"Set operation branches have different column counts "
                f"({len(left.outputs)} vs {len(right.outputs)}).",
            )
        outputs: List[Output] = []
        for index, first in enumerate(left.outputs):
            if index < len(right.outputs):
                second = right.outputs[index]
                kind = first.kind if first.kind == second.kind else "expression"
                outputs.append(
                    Output(
                        first.name,
                        _dedupe(first.deps + second.deps),
                        first.expression,
                        kind,
                        _dedupe(first.constants + second.constants),
                        _dedupe(first.filter_deps + second.filter_deps),
                    )
                )
            else:
                outputs.append(first)
        return QueryResult(outputs, complete)

    # -- SELECT ----------------------------------------------------------------
    def _resolve_select(
        self, select: exp.Select, ctes: Dict[str, QueryResult], parent: Optional[Scope], trace: bool
    ) -> QueryResult:
        scope = Scope(parent=parent, ctes=ctes)
        self._build_sources(select, scope, ctes, trace)

        outputs: List[Output] = []
        complete = True
        for item in select.expressions:
            target = item.this if isinstance(item, exp.Alias) else item
            if isinstance(target, exp.Star):
                complete &= self._expand_star(None, scope, outputs, target, trace)
            elif isinstance(target, exp.Column) and isinstance(target.this, exp.Star):
                complete &= self._expand_star(target.table.lower(), scope, outputs, target, trace)
            else:
                outputs.append(self._single_output(item, target, scope, trace))

        # Columns of these clauses shape the rows (filter lineage) and are checked for errors.
        aliases = frozenset(output.name.lower() for output in outputs)
        filters: List[str] = []
        for join in select.args.get("joins") or []:
            filters += self._clause_deps(join.args.get("on"), scope, trace)
        filters += self._clause_deps(select.args.get("where"), scope, trace)
        for key in ("group", "having", "qualify"):
            filters += self._clause_deps(select.args.get(key), scope, trace, aliases)
        self._clause_deps(select.args.get("order"), scope, trace, aliases)  # checked only
        filters = _dedupe(filters)
        for output in outputs:
            output.filter_deps = list(filters)
        return QueryResult(outputs, complete)

    def _single_output(self, item: exp.Expression, target: exp.Expression, scope: Scope, trace: bool) -> Output:
        deps, constants = self._analyze(target, scope, trace)
        if isinstance(item, exp.Alias):
            name = item.alias
        elif isinstance(target, exp.Column):
            name = target.name
        else:
            name = target.sql()
        if isinstance(target, exp.Column):
            kind = "column"
        elif not deps and constants and _is_constant(target):
            kind = "constant"
        else:
            kind = "expression"
        return Output(name, deps, target.sql(), kind, constants)

    def _expand_star(
        self, qualifier: Optional[str], scope: Scope, outputs: List[Output], node: exp.Expression, trace: bool
    ) -> bool:
        """Expand ``*`` / ``t.*``. Returns ``False`` if some columns are unknown."""
        if qualifier is None:
            sources = list(scope.sources.values())
        else:
            source = scope.sources.get(qualifier)
            if source is None:
                self._report("SQT101", Severity.ERROR, node, f"Unknown table or alias '{qualifier}' in '{node.sql()}'.")
                return False
            sources = [source]
        complete = True
        for source in sources:
            if not source.has_known_columns:
                complete = False
                self._report(
                    "SQT202", Severity.WARNING, node,
                    f"'{node.sql()}' cannot be expanded: the columns of '{source.name}' are unknown "
                    f"(provide a schema).",
                )
                continue
            for column in source.available():
                node_id = source.node_for(column)
                self._ensure_node(source, node_id, trace)
                outputs.append(Output(column, [node_id], f"{source.name}.{column}", "column"))
        return complete

    # -- sources (FROM / JOIN) -------------------------------------------------
    def _build_sources(self, select: exp.Select, scope: Scope, ctes: Dict[str, QueryResult], trace: bool) -> None:
        from_ = select.args.get("from_") or select.args.get("from")
        items = [(from_.this, None)] if from_ is not None else []
        items += [(join.this, join) for join in select.args.get("joins") or []]
        for node, join in items:
            source = self._make_source(node, ctes, trace)
            if not scope.add_source(source):
                self._report("SQT104", Severity.ERROR, node, f"Duplicate table name or alias '{source.name}'.")
            if join is None:
                continue
            for column in join.args.get("using") or []:
                scope.using.add(column.name.lower())
            if join.method == "NATURAL":
                self._report(
                    "SQT205", Severity.WARNING, join,
                    "NATURAL JOIN is not supported: the implicit join columns are not traced.",
                )

    def _make_source(self, node: exp.Expression, ctes: Dict[str, QueryResult], trace: bool) -> Source:
        if isinstance(node, exp.Table) and isinstance(node.this, exp.Identifier):
            name = node.name.lower()
            alias = node.alias.lower() if node.alias else name
            if not node.args.get("db") and not node.args.get("catalog") and name in ctes:
                return self._query_source(alias, SourceKind.CTE, ctes[name])
            qualified = ".".join(p for p in (node.catalog, node.db, node.name) if p).lower()
            columns: Optional[Dict[str, str]] = None
            table_id = qualified
            if self.schema is not None:
                key = self.schema.find(qualified)
                if key is None:
                    self._report("SQT203", Severity.WARNING, node, f"Table '{qualified}' is not in the schema.")
                else:
                    table_id = key
                    columns = {c: f"{key}.{c}" for c in self.schema.columns(key) or []}
            return Source(alias, SourceKind.TABLE, table_id, columns)
        if isinstance(node, exp.Subquery):
            alias = node.alias.lower() if node.alias else self._unique("subquery")
            result = self._resolve_query(node.this, {}, None, f"sub:{alias}", NodeKind.DERIVED, trace)
            return self._query_source(alias, SourceKind.SUBQUERY, result)
        label = node.alias or node.name or type(node).__name__.lower()
        self._report(
            "SQT204", Severity.WARNING, node,
            f"Unsupported source '{type(node).__name__}': its columns are not traced.",
        )
        return Source(label.lower(), SourceKind.UNSUPPORTED, f"unsupported:{label.lower()}", None)

    @staticmethod
    def _query_source(name: str, kind: SourceKind, result: QueryResult) -> Source:
        columns = {o.name.lower(): o.node_id for o in result.outputs} if result.complete else None
        return Source(name, kind, result.scope_id, columns)

    # -- expressions -----------------------------------------------------------
    def _clause_deps(
        self, node: Optional[exp.Expression], scope: Scope, trace: bool, aliases: frozenset = frozenset()
    ) -> List[str]:
        """Resolve the columns of a clause (reporting problems) and return what it depends on."""
        if node is None:
            return []
        return self._analyze(node, scope, trace, aliases)[0]

    def _analyze(
        self, node: exp.Expression, scope: Scope, trace: bool, aliases: frozenset = frozenset()
    ) -> "tuple[List[str], List[str]]":
        """Return (node ids the expression depends on, constants it contains)."""
        deps: List[str] = []
        constants: List[str] = []
        for part in _local_nodes(node):
            if isinstance(part, exp.Column):
                if not isinstance(part.this, exp.Star):
                    deps += self._resolve_column(part, scope, trace, aliases)
            elif isinstance(part, exp.Query):
                result = self._resolve_query(part, scope.ctes, scope, "sub:#", NodeKind.DERIVED, trace)
                deps += [output.node_id for output in result.outputs]
            elif isinstance(part, _CONSTANT_TYPES):
                constants.append(part.sql())
        return _dedupe(deps), _dedupe(constants)

    def _resolve_column(self, column: exp.Column, scope: Scope, trace: bool, aliases: frozenset) -> List[str]:
        name = column.name.lower()
        qualifier = column.table.lower() or None
        result = scope.resolve(name, qualifier)
        text = column.sql()
        if result.status is ResolutionStatus.FOUND:
            for source, node_id in result.found:
                self._ensure_node(source, node_id, trace)
            return [node_id for _, node_id in result.found]
        if result.status is ResolutionStatus.MISSING and qualifier is None and name in aliases:
            return []  # GROUP BY / HAVING / ORDER BY may refer to an output alias
        if result.status is ResolutionStatus.MISSING:
            where = f"in '{qualifier}'" if qualifier else "in any table of this query"
            self._report("SQT102", Severity.ERROR, column, f"Column '{text}' does not exist {where}"
                         + _available(result.available))
        elif result.status is ResolutionStatus.AMBIGUOUS:
            self._report("SQT103", Severity.ERROR, column,
                         f"Column '{text}' is ambiguous: it exists in {', '.join(result.candidates)}.")
        elif result.status is ResolutionStatus.UNKNOWN_QUALIFIER:
            self._report("SQT101", Severity.ERROR, column,
                         f"Unknown table or alias '{qualifier}' in '{text}'" + _available(result.available, "tables"))
        else:  # UNRESOLVABLE
            self._report("SQT201", Severity.WARNING, column,
                         f"Cannot tell where '{text}' comes from: it may belong to any of "
                         f"{', '.join(result.candidates)} (qualify it or provide a schema).")
        unresolved_id = f"?.{qualifier + '.' if qualifier else ''}{name}"
        if trace and not self.graph.has_node(unresolved_id):
            self.graph.add_node(Node(unresolved_id, NodeKind.UNRESOLVED, text))
        return [unresolved_id]

    def _ensure_node(self, source: Source, node_id: str, trace: bool) -> None:
        if not trace or self.graph.has_node(node_id):
            return
        kind = NodeKind.SOURCE if source.kind is SourceKind.TABLE else NodeKind.UNRESOLVED
        self.graph.add_node(Node(node_id, kind, node_id))

    # -- helpers -----------------------------------------------------------------
    def _unique(self, base: str) -> str:
        candidate, counter = base, 1
        if base.endswith("#"):  # anonymous scope: always numbered
            candidate = f"{base}{counter}"
        while candidate in self._used_ids:
            counter += 1
            candidate = f"{base}{counter}" if base.endswith("#") else f"{base}#{counter}"
        self._used_ids.add(candidate)
        return candidate

    def _report(self, code: str, severity: Severity, node: Optional[exp.Expression], message: str) -> None:
        self.diagnostics.append(Diagnostic(code, severity, message, self._position(node)))

    def _position(self, node: Optional[exp.Expression]) -> Optional[Position]:
        """Span of ``node`` in the SQL text, when SQLGlot recorded it."""
        if node is None:
            return None
        target = node.this if isinstance(node, exp.Column) and isinstance(node.this, exp.Identifier) else node
        meta = getattr(target, "meta", None) or {}
        if "start" not in meta or "end" not in meta:
            for child in target.walk():
                child_meta = getattr(child, "meta", None) or {}
                if "start" in child_meta and "end" in child_meta:
                    meta = child_meta
                    break
            else:
                return None
        return Position.from_offsets(self.sql, meta["start"], meta["end"] + 1)


def _local_nodes(node: exp.Expression) -> Iterator[exp.Expression]:
    """Depth-first walk that stops at subqueries (they are resolved in their own scope)."""
    yield node
    if isinstance(node, exp.Query):
        return
    for child in node.iter_expressions():
        yield from _local_nodes(child)


def _is_constant(node: exp.Expression) -> bool:
    if isinstance(node, exp.Neg):
        return _is_constant(node.this)
    return isinstance(node, _CONSTANT_TYPES)


def _dedupe(items: List[Any]) -> List[Any]:
    return list(dict.fromkeys(items))


def _available(names: List[str], what: str = "columns") -> str:
    return f" (available {what}: {', '.join(names)})." if names else "."
