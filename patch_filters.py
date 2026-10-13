from pathlib import Path


def sub(path, old, new):
    p = Path(path)
    s = p.read_text(encoding="utf-8")
    if old not in s:
        raise SystemExit(f"Texte introuvable dans {path}: {old[:60]!r}")
    p.write_text(s.replace(old, new, 1), encoding="utf-8")


G = "src/sqltrace/graph.py"
sub(G, "        self._succs: Dict[str, List[str]] = {}\n\n    # -- construction",
    "        self._succs: Dict[str, List[str]] = {}\n        self._filter_preds: Dict[str, List[str]] = {}\n\n    # -- construction")
sub(G, "            self._succs[node.id] = []\n        return",
    "            self._succs[node.id] = []\n            self._filter_preds[node.id] = []\n        return")
sub(G, "    # -- inspection ---",
'''    def add_filter_edge(self, source: str, target: str) -> None:
        """``source`` is used in a WHERE / ON / GROUP BY / HAVING that shapes ``target``'s rows."""
        for node_id in (source, target):
            if node_id not in self._nodes:
                raise KeyError(f"Unknown node '{node_id}'")
        if source not in self._filter_preds[target]:
            self._filter_preds[target].append(source)

    # -- inspection ---''')
sub(G, "    def _reach(self,",
'''    def filter_edges(self) -> List[Tuple[str, str]]:
        return [(src, dst) for dst, sources in self._filter_preds.items() for src in sources]

    def filter_sources(self, node_id: str) -> List[str]:
        """Real-table columns that filter/group the rows ``node_id`` is computed from."""
        found: List[str] = []
        for current in [node_id] + self.upstream(node_id):
            for dep in self._filter_preds[current]:
                if self._nodes[dep].kind is NodeKind.SOURCE:
                    candidates = [dep]
                else:  # a subquery output: its own sources and its own filters count too
                    candidates = self.base_sources(dep) + self.filter_sources(dep)
                for name in candidates:
                    if name not in found:
                        found.append(name)
        return found

    def _reach(self,''')
sub(G, '''            "edges": [{"source": src, "target": dst} for src, dst in self.edges()],''',
'''            "edges": [{"source": src, "target": dst} for src, dst in self.edges()],
            "filter_edges": [{"source": src, "target": dst} for src, dst in self.filter_edges()],''')
sub(G, '''            lines.append(f'  "{_dot(src)}" -> "{_dot(dst)}";')
''', '''            lines.append(f'  "{_dot(src)}" -> "{_dot(dst)}";')
        for src, dst in self.filter_edges():
            lines.append(f'  "{_dot(src)}" -> "{_dot(dst)}" [style=dashed];')
''')
sub(G, '''            lines.append(f"    {ids[src]} --> {ids[dst]}")
''', '''            lines.append(f"    {ids[src]} --> {ids[dst]}")
        for src, dst in self.filter_edges():
            lines.append(f"    {ids[src]} -.-> {ids[dst]}")
''')

R = "src/sqltrace/resolver.py"
sub(R, '    constants: List[str] = field(default_factory=list)\n    node_id: str = ""',
    '    constants: List[str] = field(default_factory=list)\n    filter_deps: List[str] = field(default_factory=list)  # WHERE / ON / GROUP BY / HAVING columns\n    node_id: str = ""')
sub(R, '''                for dep in output.deps:
                    self.graph.add_edge(dep, node_id)''',
'''                for dep in output.deps:
                    self.graph.add_edge(dep, node_id)
                for dep in output.filter_deps:
                    self.graph.add_filter_edge(dep, node_id)''')
sub(R, '''                        _dedupe(first.constants + second.constants),
                    )''', '''                        _dedupe(first.constants + second.constants),
                        _dedupe(first.filter_deps + second.filter_deps),
                    )''')
sub(R, '''        # Other clauses are not part of the lineage, but their columns are still checked.
        aliases = {output.name.lower() for output in outputs}
        for join in select.args.get("joins") or []:
            self._check(join.args.get("on"), scope)
        self._check(select.args.get("where"), scope)
        for key in ("group", "having", "qualify", "order"):
            self._check(select.args.get(key), scope, aliases)
        return QueryResult(outputs, complete)''',
'''        # Columns of these clauses shape the rows (filter lineage) and are checked for errors.
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
        return QueryResult(outputs, complete)''')
sub(R, '''    def _check(self, node: Optional[exp.Expression], scope: Scope, aliases: frozenset = frozenset()) -> None:
        """Resolve columns of a clause only to report problems (nothing is added to the graph)."""
        if node is not None:
            self._analyze(node, scope, trace=False, aliases=aliases)''',
'''    def _clause_deps(
        self, node: Optional[exp.Expression], scope: Scope, trace: bool, aliases: frozenset = frozenset()
    ) -> List[str]:
        """Resolve the columns of a clause (reporting problems) and return what it depends on."""
        if node is None:
            return []
        return self._analyze(node, scope, trace, aliases)[0]''')

A = "src/sqltrace/analyzer.py"
sub(A, "    constants: List[str] = field(default_factory=list)\n\n    def to_dict",
    "    constants: List[str] = field(default_factory=list)\n    filter_sources: List[str] = field(default_factory=list)  # columns in WHERE/ON/GROUP BY/HAVING\n\n    def to_dict")
sub(A, '''            "constants": self.constants,
        }''', '''            "constants": self.constants,
            "filter_sources": self.filter_sources,
        }''')
sub(A, "            constants=o.constants,\n        )",
    "            constants=o.constants,\n            filter_sources=graph.filter_sources(o.node_id),\n        )")

P = "src/sqltrace/report.py"
sub(P, '''        if column.constants:
            lines.append(f"      constants: {', '.join(column.constants)}")
    if result.diagnostics:''', '''        if column.constants:
            lines.append(f"      constants: {', '.join(column.constants)}")
        if column.filter_sources:
            lines.append(f"      filtered by: {', '.join(column.filter_sources)}")
    if result.diagnostics:''')
sub(P, '''    if column.constants:
        lines.append("Constants:               " + ", ".join(column.constants))
    return''', '''    if column.constants:
        lines.append("Constants:               " + ", ".join(column.constants))
    if column.filter_sources:
        lines.append("Filtered/grouped by:     " + ", ".join(column.filter_sources))
    return''')

print("OK : les 4 fichiers sont modifies.")