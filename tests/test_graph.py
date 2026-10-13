"""Version 0.4: lineage graph queries and exports."""

import json

from sqltrace import LineageGraph, Node, NodeKind, analyze

SQL = """
WITH ventes AS (SELECT client_id, prix * quantite AS total FROM commande)
SELECT client_id, SUM(total) AS chiffre FROM ventes GROUP BY client_id
"""


def test_direct_and_transitive_dependencies():
    graph = analyze(SQL).graph
    assert graph.direct_sources("query.chiffre") == ["cte:ventes.total"]
    assert graph.upstream("query.chiffre") == [
        "cte:ventes.total",
        "commande.prix",
        "commande.quantite",
    ]
    assert graph.base_sources("query.chiffre") == ["commande.prix", "commande.quantite"]


def test_downstream_impact_of_a_source_column():
    graph = analyze(SQL).graph
    assert graph.downstream("commande.prix") == ["cte:ventes.total", "query.chiffre"]
    assert graph.direct_dependents("commande.prix") == ["cte:ventes.total"]


def test_graph_is_acyclic_by_construction_and_has_no_duplicate_edges():
    graph = analyze("SELECT a + a AS s FROM t").graph
    assert graph.edges() == [("t.a", "query.s")]


def test_node_kinds():
    graph = analyze(SQL).graph
    assert graph.node("commande.prix").kind is NodeKind.SOURCE
    assert graph.node("cte:ventes.total").kind is NodeKind.DERIVED
    assert graph.node("query.chiffre").kind is NodeKind.OUTPUT


def test_unresolved_node_for_bad_reference():
    graph = analyze("SELECT nope FROM t JOIN u ON t.id = u.id").graph
    assert graph.node("?.nope").kind is NodeKind.UNRESOLVED
    assert graph.base_sources("query.nope") == []


def test_explain_tree_shows_each_level():
    tree = analyze(SQL).graph.explain("query.chiffre")
    assert tree.splitlines()[0].startswith("query.chiffre")
    assert "cte:ventes.total" in tree and "commande.prix" in tree and "commande.quantite" in tree


def test_to_dict_is_json_serialisable():
    result = analyze(SQL)
    data = json.loads(json.dumps(result.to_dict()))
    assert data["ok"] is True
    assert {"source": "commande.prix", "target": "cte:ventes.total"} in data["graph"]["edges"]
    assert data["outputs"][1]["base_sources"] == ["commande.prix", "commande.quantite"]


def test_dot_export():
    dot = analyze("SELECT a AS x FROM t").graph.to_dot()
    assert dot.startswith("digraph lineage {") and '"t.a" -> "query.x";' in dot


def test_mermaid_export():
    mermaid = analyze("SELECT a AS x FROM t").graph.to_mermaid()
    assert mermaid.startswith("flowchart LR") and "-->" in mermaid and ":::source" in mermaid


def test_graph_rejects_edges_between_unknown_nodes():
    graph = LineageGraph()
    graph.add_node(Node("a", NodeKind.SOURCE, "a"))
    try:
        graph.add_edge("a", "b")
    except KeyError:
        return
    raise AssertionError("expected KeyError")
