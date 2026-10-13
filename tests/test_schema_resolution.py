"""Version 0.2: qualified/unqualified names, table aliases, schema-based diagnostics."""

import json

import pytest

from sqltrace import Schema, SchemaError, Severity, analyze

SCHEMA = {
    "client": ["id", "nom", "ville"],
    "commande": ["id", "client_id", "prix", "quantite"],
}


def codes(result, severity=None):
    return [d.code for d in result.diagnostics if severity is None or d.severity is severity]


def test_table_alias_is_resolved_to_real_table():
    result = analyze("SELECT c.prix AS p FROM Commande c", SCHEMA)
    assert result.output("p").base_sources == ["commande.prix"]


def test_qualified_with_table_name_when_no_alias():
    result = analyze("SELECT commande.prix FROM commande", SCHEMA)
    assert result.output("prix").base_sources == ["commande.prix"]


def test_unknown_column_is_certain_error_with_position():
    result = analyze("SELECT prix, couleur FROM commande", SCHEMA)
    (diag,) = result.diagnostics
    assert diag.code == "SQT102" and diag.severity is Severity.ERROR
    assert diag.position.column == 14 and diag.position.line == 1
    assert "couleur" in diag.message and not result.ok


def test_unknown_qualified_column():
    result = analyze("SELECT c.couleur FROM commande c", SCHEMA)
    assert codes(result) == ["SQT102"]


def test_unknown_qualifier():
    result = analyze("SELECT x.prix FROM commande c", SCHEMA)
    assert codes(result) == ["SQT101"]


def test_original_name_is_hidden_by_alias():
    result = analyze("SELECT commande.prix FROM commande c", SCHEMA)
    assert codes(result) == ["SQT101"]


def test_ambiguous_reference_with_schema():
    result = analyze("SELECT id FROM client JOIN commande ON client.id = commande.client_id", SCHEMA)
    assert codes(result) == ["SQT103"]
    assert "client" in result.diagnostics[0].message and "commande" in result.diagnostics[0].message


def test_unambiguous_unqualified_in_join():
    result = analyze("SELECT nom, prix FROM client JOIN commande ON client.id = commande.client_id", SCHEMA)
    assert result.ok and not result.diagnostics
    assert result.output("nom").base_sources == ["client.nom"]
    assert result.output("prix").base_sources == ["commande.prix"]


def test_without_schema_single_table_is_attributed():
    result = analyze("SELECT prix FROM commande")
    assert result.output("prix").base_sources == ["commande.prix"] and not result.diagnostics


def test_without_schema_two_tables_is_unresolvable_warning_not_error():
    result = analyze("SELECT prix FROM commande c JOIN client k ON c.client_id = k.id")
    (diag,) = result.diagnostics
    assert diag.code == "SQT201" and diag.severity is Severity.WARNING
    assert result.ok  # a warning is not a certain error
    assert result.output("prix").base_sources == []


def test_partial_schema_known_and_unknown_table():
    result = analyze("SELECT prix FROM commande c JOIN autre a ON a.id = c.id", {"commande": ["id", "prix"]})
    assert codes(result) == ["SQT203", "SQT201"] or set(codes(result)) == {"SQT203", "SQT201"}
    assert all(d.severity is Severity.WARNING for d in result.diagnostics)


def test_only_unknown_table_left_gets_the_column():
    result = analyze("SELECT zzz FROM commande c JOIN autre a ON a.id = c.id", {"commande": ["id", "prix"]})
    assert result.output("zzz").base_sources == ["autre.zzz"]


def test_table_missing_from_schema_warns():
    result = analyze("SELECT a FROM inconnue", SCHEMA)
    assert codes(result) == ["SQT203"] and result.ok


def test_where_group_having_order_columns_are_checked():
    result = analyze(
        "SELECT prix FROM commande WHERE nope1 > 1 GROUP BY nope2 HAVING SUM(nope3) > 0 ORDER BY nope4", SCHEMA
    )
    assert codes(result) == ["SQT102"] * 4


def test_output_alias_allowed_in_order_by_and_group_by():
    result = analyze("SELECT prix * quantite AS total FROM commande GROUP BY total ORDER BY total", SCHEMA)
    assert result.ok and not result.diagnostics


def test_star_expansion_with_schema():
    result = analyze("SELECT * FROM client", SCHEMA)
    assert [o.name for o in result.outputs] == ["id", "nom", "ville"]
    assert result.output("nom").base_sources == ["client.nom"]


def test_qualified_star():
    result = analyze("SELECT c.* FROM commande c JOIN client k ON c.client_id = k.id", SCHEMA)
    assert [o.name for o in result.outputs] == ["id", "client_id", "prix", "quantite"]


def test_star_without_schema_is_a_warning_and_yields_no_columns():
    result = analyze("SELECT * FROM t")
    assert codes(result) == ["SQT202"] and result.outputs == []


def test_case_insensitive_schema():
    result = analyze("SELECT PRIX FROM COMMANDE", {"Commande": ["Prix"]})
    assert result.ok and result.output("prix").base_sources == ["commande.prix"]


def test_schema_accepts_type_mapping_and_qualified_names():
    schema = Schema({"sales.orders": {"id": "INT", "amount": "DECIMAL"}})
    assert analyze("SELECT amount FROM orders", schema).output("amount").base_sources == ["sales.orders.amount"]
    assert analyze("SELECT amount FROM sales.orders", schema).ok


def test_duplicate_alias_is_reported():
    result = analyze("SELECT a.id FROM client a JOIN commande a ON 1 = 1", SCHEMA)
    assert "SQT104" in codes(result)


def test_schema_file_loading(tmp_path):
    path = tmp_path / "schema.json"
    path.write_text(json.dumps(SCHEMA), encoding="utf-8")
    assert Schema.from_file(path).columns("client") == ["id", "nom", "ville"]


@pytest.mark.parametrize("content", ["not json", "[1, 2]", '{"t": "id"}', '{"t": [1]}', '{"": ["a"]}'])
def test_invalid_schema_files(tmp_path, content):
    path = tmp_path / "bad.json"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(SchemaError):
        Schema.from_file(path)


def test_missing_schema_file():
    with pytest.raises(SchemaError):
        Schema.from_file("/nonexistent/schema.json")
