"""Version 0.3: joins, subqueries, CTEs, set operations, multi-level propagation."""

import pytest

from sqltrace import UnsupportedSQLError, analyze

SCHEMA = {
    "client": ["id", "nom", "ville"],
    "commande": ["id", "client_id", "prix", "quantite"],
}


def codes(result):
    return [d.code for d in result.diagnostics]


def test_join_with_aliases():
    sql = """
    SELECT k.nom, c.prix * c.quantite AS total
    FROM commande c JOIN client k ON c.client_id = k.id
    """
    result = analyze(sql, SCHEMA)
    assert result.ok and not result.diagnostics
    assert result.output("nom").base_sources == ["client.nom"]
    assert result.output("total").base_sources == ["commande.prix", "commande.quantite"]


def test_left_join_and_comma_join():
    assert analyze("SELECT k.nom FROM commande c LEFT JOIN client k ON k.id = c.client_id", SCHEMA).ok
    assert analyze("SELECT k.nom, c.prix FROM commande c, client k WHERE k.id = c.client_id", SCHEMA).ok


def test_self_join_resolves_both_aliases_to_same_table():
    result = analyze("SELECT a.prix AS p1, b.prix AS p2 FROM commande a JOIN commande b ON a.id = b.id", SCHEMA)
    assert result.output("p1").base_sources == ["commande.prix"]
    assert result.output("p2").base_sources == ["commande.prix"]


def test_join_using_does_not_make_the_column_ambiguous():
    result = analyze("SELECT id FROM client JOIN commande USING (id)", SCHEMA)
    assert result.ok and not result.diagnostics
    assert result.output("id").base_sources == ["client.id", "commande.id"]


def test_natural_join_is_flagged():
    result = analyze("SELECT nom FROM client NATURAL JOIN commande", SCHEMA)
    assert "SQT205" in codes(result)


def test_derived_table_subquery():
    sql = "SELECT s.total * 2 AS double FROM (SELECT prix * quantite AS total FROM commande) s"
    result = analyze(sql, SCHEMA)
    double = result.output("double")
    assert double.direct_sources == ["sub:s.total"]
    assert double.base_sources == ["commande.prix", "commande.quantite"]


def test_derived_table_hides_inner_columns():
    result = analyze("SELECT s.prix FROM (SELECT prix * quantite AS total FROM commande) s", SCHEMA)
    assert codes(result) == ["SQT102"]


def test_nested_subqueries_three_levels():
    sql = """
    SELECT z.c AS final FROM (
      SELECT y.b + 1 AS c FROM (SELECT a * 2 AS b FROM t) y
    ) z
    """
    result = analyze(sql)
    final = result.output("final")
    assert final.base_sources == ["t.a"]
    assert result.graph.upstream("query.final") == ["sub:z.c", "sub:y.b", "t.a"]


def test_cte_basic():
    sql = """
    WITH ventes AS (SELECT client_id, prix * quantite AS total FROM commande)
    SELECT client_id, total FROM ventes
    """
    result = analyze(sql, SCHEMA)
    assert result.ok
    assert result.output("total").direct_sources == ["cte:ventes.total"]
    assert result.output("total").base_sources == ["commande.prix", "commande.quantite"]
    assert result.output("client_id").base_sources == ["commande.client_id"]


def test_chained_ctes_and_join_between_them():
    sql = """
    WITH a AS (SELECT id, prix FROM commande),
         b AS (SELECT id, prix * 2 AS p2 FROM a)
    SELECT a.id, b.p2 FROM a JOIN b ON a.id = b.id
    """
    result = analyze(sql, SCHEMA)
    assert result.ok and not result.diagnostics
    assert result.output("p2").base_sources == ["commande.prix"]
    assert result.output("id").base_sources == ["commande.id"]


def test_cte_column_list_renames_columns():
    result = analyze("WITH c (x, y) AS (SELECT prix, quantite FROM commande) SELECT x FROM c", SCHEMA)
    assert result.output("x").base_sources == ["commande.prix"]


def test_cte_alias_in_from():
    result = analyze("WITH c AS (SELECT prix FROM commande) SELECT t.prix FROM c t", SCHEMA)
    assert result.output("prix").base_sources == ["commande.prix"]


def test_cte_shadows_real_table_name():
    result = analyze("WITH commande AS (SELECT nom AS prix FROM client) SELECT prix FROM commande", SCHEMA)
    assert result.output("prix").base_sources == ["client.nom"]


def test_cte_error_is_reported_where_it_happens():
    result = analyze("WITH c AS (SELECT nope FROM commande) SELECT nope FROM c", SCHEMA)
    assert codes(result) == ["SQT102"]
    assert result.diagnostics[0].position.column == 19


def test_star_through_cte_with_schema():
    result = analyze("WITH c AS (SELECT * FROM client) SELECT nom FROM c", SCHEMA)
    assert result.output("nom").base_sources == ["client.nom"]


def test_star_through_cte_without_schema_is_not_invented():
    result = analyze("WITH c AS (SELECT * FROM client) SELECT nom FROM c")
    assert "SQT202" in codes(result)
    assert result.output("nom").base_sources == []


def test_scalar_subquery_in_select_list():
    sql = "SELECT k.nom, (SELECT MAX(c.prix) FROM commande c WHERE c.client_id = k.id) AS max_prix FROM client k"
    result = analyze(sql, SCHEMA)
    assert result.ok and not result.diagnostics
    assert result.output("max_prix").base_sources == ["commande.prix"]


def test_correlated_reference_to_outer_scope():
    sql = "SELECT k.nom FROM client k WHERE EXISTS (SELECT 1 FROM commande c WHERE c.client_id = k.id)"
    assert analyze(sql, SCHEMA).ok


def test_unknown_column_inside_where_subquery():
    result = analyze("SELECT nom FROM client WHERE id IN (SELECT nope FROM commande)", SCHEMA)
    assert codes(result) == ["SQT102"]


def test_subquery_in_where_does_not_pollute_graph():
    result = analyze("SELECT nom FROM client WHERE id IN (SELECT client_id FROM commande)", SCHEMA)
    assert result.graph.upstream("query.nom") == ["client.nom"]


def test_union_merges_dependencies_by_position():
    result = analyze("SELECT nom AS n FROM client UNION ALL SELECT ville FROM client", SCHEMA)
    assert result.output("n").base_sources == ["client.nom", "client.ville"]


def test_union_column_count_mismatch():
    result = analyze("SELECT nom, ville FROM client UNION SELECT nom FROM client", SCHEMA)
    assert "SQT105" in codes(result)


def test_union_inside_cte():
    sql = "WITH u AS (SELECT prix AS v FROM commande UNION SELECT id FROM client) SELECT v FROM u"
    result = analyze(sql, SCHEMA)
    assert result.output("v").base_sources == ["commande.prix", "client.id"]


def test_window_function_depends_on_partition_and_order_columns():
    result = analyze("SELECT SUM(prix) OVER (PARTITION BY client_id ORDER BY id) AS run FROM commande", SCHEMA)
    assert set(result.output("run").base_sources) == {"commande.prix", "commande.client_id", "commande.id"}


def test_recursive_cte_is_rejected_explicitly():
    with pytest.raises(UnsupportedSQLError):
        analyze("WITH RECURSIVE r AS (SELECT 1 AS n UNION ALL SELECT n + 1 FROM r) SELECT n FROM r")


def test_unsupported_source_is_a_warning():
    result = analyze("SELECT x FROM UNNEST([1, 2]) AS x")
    assert "SQT204" in codes(result)


def test_duplicate_output_names_get_distinct_nodes():
    result = analyze("SELECT prix, prix FROM commande", SCHEMA)
    assert [o.node_id for o in result.outputs] == ["query.prix", "query.prix#2"]
