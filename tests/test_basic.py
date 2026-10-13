"""Version 0.1: single SELECT, one table, constants, expressions, syntax errors."""

import pytest

from sqltrace import SQLSyntaxError, UnsupportedSQLError, analyze


def test_expression_depends_on_both_columns():
    result = analyze("SELECT prix * quantite AS total FROM Commande")
    total = result.output("total")
    assert total.kind == "expression"
    assert total.expression == "prix * quantite"
    assert total.base_sources == ["commande.prix", "commande.quantite"]
    assert result.ok and not result.diagnostics


def test_plain_column_and_alias():
    result = analyze("SELECT prix, quantite AS qty FROM Commande")
    assert [o.name for o in result.outputs] == ["prix", "qty"]
    assert result.output("qty").kind == "column"
    assert result.output("qty").base_sources == ["commande.quantite"]


def test_constants_are_reported_not_traced():
    result = analyze("SELECT 1 AS one, 'x' AS label, prix + 10 AS p FROM t")
    assert result.output("one").kind == "constant"
    assert result.output("one").base_sources == []
    assert result.output("label").constants == ["'x'"]
    assert result.output("p").constants == ["10"]
    assert result.output("p").base_sources == ["t.prix"]


def test_function_and_case_expressions():
    result = analyze("SELECT COALESCE(a, b) AS c, CASE WHEN x > 0 THEN y ELSE 0 END AS d, COUNT(*) AS n FROM t")
    assert result.output("c").base_sources == ["t.a", "t.b"]
    assert result.output("d").base_sources == ["t.x", "t.y"]
    assert result.output("n").base_sources == []


def test_unaliased_expression_is_named_by_its_sql():
    result = analyze("SELECT prix * 2 FROM t")
    assert result.outputs[0].name == "prix * 2"


def test_identifiers_are_case_insensitive():
    result = analyze("SELECT PRIX FROM Commande")
    assert result.output("prix").base_sources == ["commande.prix"]


def test_graph_edges_for_simple_query():
    graph = analyze("SELECT a + b AS s FROM t").graph
    assert set(graph.edges()) == {("t.a", "query.s"), ("t.b", "query.s")}


@pytest.mark.parametrize("sql", ["SELEC a FROM t", "SELECT FROM WHERE x", "SELECT a FROM", "SELECT (a FROM t"])
def test_syntax_errors(sql):
    with pytest.raises(SQLSyntaxError):
        analyze(sql)


def test_syntax_error_has_position():
    with pytest.raises(SQLSyntaxError) as info:
        analyze("SELECT a\nFROM t\nWHERE")
    assert info.value.position is not None
    assert info.value.position.line >= 1


@pytest.mark.parametrize("sql", ["", "   \n "])
def test_empty_query(sql):
    with pytest.raises(SQLSyntaxError):
        analyze(sql)


@pytest.mark.parametrize(
    "sql",
    ["SELECT 1; SELECT 2", "INSERT INTO t VALUES (1)", "CREATE TABLE t (a INT)", "DROP TABLE t"],
)
def test_unsupported_statements(sql):
    with pytest.raises(UnsupportedSQLError):
        analyze(sql)


def test_unknown_dialect_is_reported_as_error():
    with pytest.raises(SQLSyntaxError):
        analyze("SELECT 1", dialect="not-a-dialect")
