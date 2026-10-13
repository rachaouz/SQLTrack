"""Longer, realistic queries mixing CTEs, joins, subqueries, windows and filters."""

from sqltrace import analyze

SCHEMA = {
    "clients": ["id", "nom", "pays", "date_inscription"],
    "commandes": ["id", "client_id", "date_commande", "statut", "remise"],
    "lignes": ["commande_id", "produit_id", "prix_unitaire", "quantite"],
    "produits": ["id", "libelle", "categorie_id", "cout"],
    "categories": ["id", "nom"],
}

REPORT = """
WITH lignes_valides AS (
    SELECT l.commande_id, l.produit_id,
           l.prix_unitaire * l.quantite AS montant_brut
    FROM lignes l
    JOIN commandes c ON c.id = l.commande_id
    WHERE c.statut <> 'annulee'
),
ca_client AS (
    SELECT c.client_id,
           SUM(lv.montant_brut * (1 - c.remise)) AS ca_net,
           COUNT(DISTINCT c.id) AS nb_commandes
    FROM commandes c
    JOIN lignes_valides lv ON lv.commande_id = c.id
    GROUP BY c.client_id
),
classement AS (
    SELECT cc.*,
           RANK() OVER (ORDER BY cc.ca_net DESC) AS rang
    FROM ca_client cc
)
SELECT cl.nom,
       cl.pays,
       cs.ca_net,
       cs.nb_commandes,
       cs.rang,
       CASE WHEN cs.rang <= 10 THEN 'top' ELSE 'standard' END AS segment,
       (SELECT MAX(c2.date_commande) FROM commandes c2 WHERE c2.client_id = cl.id) AS derniere_commande
FROM classement cs
JOIN clients cl ON cl.id = cs.client_id
WHERE cl.pays IN (SELECT pays FROM clients GROUP BY pays HAVING COUNT(*) > 5)
ORDER BY cs.rang
"""


def test_multi_cte_report_has_no_diagnostics():
    result = analyze(REPORT, SCHEMA)
    assert result.ok and not result.diagnostics
    assert [o.name for o in result.outputs] == [
        "nom", "pays", "ca_net", "nb_commandes", "rang", "segment", "derniere_commande",
    ]


def test_deep_propagation_through_three_ctes():
    result = analyze(REPORT, SCHEMA)
    assert set(result.output("ca_net").base_sources) == {
        "lignes.prix_unitaire", "lignes.quantite", "commandes.remise",
    }
    # rank depends on ca_net through the window ORDER BY
    assert set(result.output("rang").base_sources) == set(result.output("ca_net").base_sources)
    assert set(result.output("segment").base_sources) == set(result.output("ca_net").base_sources)
    assert result.output("segment").constants == ["10", "'top'", "'standard'"]


def test_scalar_subquery_and_plain_columns():
    result = analyze(REPORT, SCHEMA)
    assert result.output("derniere_commande").base_sources == ["commandes.date_commande"]
    assert result.output("nom").base_sources == ["clients.nom"]


def test_filter_lineage_collects_where_on_group_by_having():
    result = analyze(REPORT, SCHEMA)
    filters = set(result.output("ca_net").filter_sources)
    assert "commandes.statut" in filters  # WHERE inside the first CTE
    assert "commandes.client_id" in filters  # GROUP BY in the second CTE
    assert "clients.pays" in filters  # WHERE ... IN (subquery) of the final query
    assert "lignes.commande_id" in filters  # JOIN ... ON
    assert "lignes.quantite" not in filters  # only used as a value, not as a filter


def test_filter_sources_are_separate_from_value_sources():
    result = analyze("SELECT nom FROM clients WHERE pays = 'DZ'", SCHEMA)
    nom = result.output("nom")
    assert nom.base_sources == ["clients.nom"]
    assert nom.filter_sources == ["clients.pays"]
    assert result.graph.upstream("query.nom") == ["clients.nom"]


def test_join_condition_columns_count_as_filters():
    result = analyze("SELECT cl.nom FROM clients cl JOIN commandes c ON c.client_id = cl.id", SCHEMA)
    assert set(result.output("nom").filter_sources) == {"commandes.client_id", "clients.id"}


def test_order_by_is_checked_but_not_a_filter():
    result = analyze("SELECT nom FROM clients ORDER BY pays", SCHEMA)
    assert result.output("nom").filter_sources == []
    bad = analyze("SELECT nom FROM clients ORDER BY inconnu", SCHEMA)
    assert [d.code for d in bad.diagnostics] == ["SQT102"]


def test_filter_edges_exported():
    graph = analyze("SELECT nom FROM clients WHERE pays = 'DZ'", SCHEMA).graph
    assert graph.filter_edges() == [("clients.pays", "query.nom")]
    assert "style=dashed" in graph.to_dot() and "-.->" in graph.to_mermaid()
    assert graph.to_dict()["filter_edges"] == [{"source": "clients.pays", "target": "query.nom"}]


def test_errors_are_found_deep_inside_a_long_query():
    broken = REPORT.replace("c.remise)", "c.rabais)")
    result = analyze(broken, SCHEMA)
    (diag,) = [d for d in result.diagnostics if d.severity.value == "error"]
    assert diag.code == "SQT102" and "rabais" in diag.message
    assert diag.position.line == 11  # the SUM(...) line in the second CTE


ANALYTICS = """
SELECT cat.nom AS categorie,
       p.libelle,
       SUM(l.prix_unitaire * l.quantite) - SUM(p.cout * l.quantite) AS marge,
       SUM(l.quantite) * 1.0 / NULLIF(COUNT(DISTINCT l.commande_id), 0) AS qte_moyenne,
       LAG(SUM(l.quantite)) OVER (PARTITION BY cat.id ORDER BY p.id) AS qte_precedente
FROM lignes l
JOIN produits p ON p.id = l.produit_id
LEFT JOIN categories cat ON cat.id = p.categorie_id
WHERE EXISTS (SELECT 1 FROM commandes c WHERE c.id = l.commande_id AND c.statut = 'livree')
GROUP BY cat.id, cat.nom, p.id, p.libelle
HAVING SUM(l.quantite) > 0
"""


def test_analytics_query_with_aggregates_windows_and_exists():
    result = analyze(ANALYTICS, SCHEMA)
    assert result.ok and not result.diagnostics
    assert set(result.output("marge").base_sources) == {
        "lignes.prix_unitaire", "lignes.quantite", "produits.cout",
    }
    assert set(result.output("qte_precedente").base_sources) == {
        "lignes.quantite", "categories.id", "produits.id",
    }
    assert result.output("categorie").base_sources == ["categories.nom"]
    assert "commandes.statut" in result.output("marge").filter_sources


def test_union_of_two_reports_with_filters():
    sql = """
    SELECT nom AS libelle FROM clients WHERE pays = 'DZ'
    UNION ALL
    SELECT libelle FROM produits WHERE cout > 0
    """
    result = analyze(sql, SCHEMA)
    out = result.output("libelle")
    assert out.base_sources == ["clients.nom", "produits.libelle"]
    assert out.filter_sources == ["clients.pays", "produits.cout"]