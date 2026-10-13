"""Directed lineage graph: an edge ``a -> b`` means "b is computed from a"."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, List, Optional, Set, Tuple


class NodeKind(str, Enum):
    SOURCE = "source"  # a column of a real table
    DERIVED = "derived"  # an output column of a CTE or subquery
    OUTPUT = "output"  # an output column of the analysed query
    UNRESOLVED = "unresolved"  # a reference that could not be pinned down


@dataclass(frozen=True)
class Node:
    id: str
    kind: NodeKind
    label: str
    expression: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {"id": self.id, "kind": self.kind.value, "label": self.label, "expression": self.expression}


class LineageGraph:
    """A small acyclic graph with the queries lineage needs (no external dependency)."""

    def __init__(self) -> None:
        self._nodes: Dict[str, Node] = {}
        self._preds: Dict[str, List[str]] = {}
        self._succs: Dict[str, List[str]] = {}

    # -- construction -------------------------------------------------------
    def add_node(self, node: Node) -> Node:
        """Add ``node``; an existing node with the same id is kept."""
        if node.id not in self._nodes:
            self._nodes[node.id] = node
            self._preds[node.id] = []
            self._succs[node.id] = []
        return self._nodes[node.id]

    def add_edge(self, source: str, target: str) -> None:
        for node_id in (source, target):
            if node_id not in self._nodes:
                raise KeyError(f"Unknown node '{node_id}'")
        if target not in self._succs[source]:
            self._succs[source].append(target)
            self._preds[target].append(source)

    # -- inspection ---------------------------------------------------------
    def has_node(self, node_id: str) -> bool:
        return node_id in self._nodes

    def node(self, node_id: str) -> Node:
        return self._nodes[node_id]

    def nodes(self) -> List[Node]:
        return list(self._nodes.values())

    def edges(self) -> List[Tuple[str, str]]:
        return [(src, dst) for src, targets in self._succs.items() for dst in targets]

    def direct_sources(self, node_id: str) -> List[str]:
        """Nodes ``node_id`` is computed from directly."""
        return list(self._preds[node_id])

    def direct_dependents(self, node_id: str) -> List[str]:
        return list(self._succs[node_id])

    def upstream(self, node_id: str) -> List[str]:
        """All direct and transitive sources (nearest first, no duplicates)."""
        return self._reach(node_id, self._preds)

    def downstream(self, node_id: str) -> List[str]:
        return self._reach(node_id, self._succs)

    def base_sources(self, node_id: str) -> List[str]:
        """Real-table columns ``node_id`` ultimately depends on."""
        return [n for n in self.upstream(node_id) if self._nodes[n].kind is NodeKind.SOURCE]

    def _reach(self, start: str, links: Dict[str, List[str]]) -> List[str]:
        seen: Set[str] = {start}
        order: List[str] = []
        frontier = [start]
        while frontier:
            next_frontier: List[str] = []
            for current in frontier:
                for neighbour in links[current]:
                    if neighbour not in seen:
                        seen.add(neighbour)
                        order.append(neighbour)
                        next_frontier.append(neighbour)
            frontier = next_frontier
        return order

    # -- rendering ----------------------------------------------------------
    def explain(self, node_id: str) -> str:
        """Text tree showing how ``node_id`` is derived, level by level."""
        lines: List[str] = []

        def describe(current: str) -> str:
            node = self._nodes[current]
            if node.expression and node.kind in (NodeKind.DERIVED, NodeKind.OUTPUT):
                return f"{current}  = {node.expression}"
            if node.kind is NodeKind.UNRESOLVED:
                return f"{current}  (unresolved)"
            return current

        def walk(current: str, prefix: str, is_last: bool, is_root: bool) -> None:
            if is_root:
                lines.append(describe(current))
                child_prefix = ""
            else:
                lines.append(prefix + ("└── " if is_last else "├── ") + describe(current))
                child_prefix = prefix + ("    " if is_last else "│   ")
            children = self._preds[current]
            for index, child in enumerate(children):
                walk(child, child_prefix, index == len(children) - 1, False)

        walk(node_id, "", True, True)
        return "\n".join(lines)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "nodes": [node.to_dict() for node in self._nodes.values()],
            "edges": [{"source": src, "target": dst} for src, dst in self.edges()],
        }

    def to_dot(self) -> str:
        """Graphviz DOT source (render with ``dot -Tpng``)."""
        colors = {
            NodeKind.SOURCE: "#dbeafe",
            NodeKind.DERIVED: "#fef9c3",
            NodeKind.OUTPUT: "#dcfce7",
            NodeKind.UNRESOLVED: "#fee2e2",
        }
        lines = ["digraph lineage {", "  rankdir=LR;", '  node [shape=box, style=filled, fontname="Helvetica"];']
        for node in self._nodes.values():
            lines.append(f'  "{_dot(node.id)}" [label="{_dot(node.id)}", fillcolor="{colors[node.kind]}"];')
        for src, dst in self.edges():
            lines.append(f'  "{_dot(src)}" -> "{_dot(dst)}";')
        lines.append("}")
        return "\n".join(lines)

    def to_mermaid(self) -> str:
        """Mermaid flowchart source (renders natively on GitHub)."""
        ids = {node_id: f"n{index}" for index, node_id in enumerate(self._nodes)}
        lines = ["flowchart LR"]
        for node in self._nodes.values():
            label = node.id.replace('"', "#quot;")
            lines.append(f'    {ids[node.id]}["{label}"]:::{node.kind.value}')
        for src, dst in self.edges():
            lines.append(f"    {ids[src]} --> {ids[dst]}")
        lines += [
            "    classDef source fill:#dbeafe,stroke:#1d4ed8",
            "    classDef derived fill:#fef9c3,stroke:#a16207",
            "    classDef output fill:#dcfce7,stroke:#15803d",
            "    classDef unresolved fill:#fee2e2,stroke:#b91c1c",
        ]
        return "\n".join(lines)


def _dot(text: str) -> str:
    return text.replace("\\", "\\\\").replace('"', '\\"')
