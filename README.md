# SQLTrace

Column-level lineage for SQL queries. Give SQLTrace a query and it tells you, for every output column, **which table columns it really comes from** — through aliases, joins, subqueries and CTEs.

```sql
SELECT prix * quantite AS total FROM commande;
```

```text
$ sqltrace analyze -f examples/01_simple.sql
Output columns:
  total  [expression]  prix * quantite
      from: commande.prix, commande.quantite
```

SQLTrace does **not** parse SQL itself: [SQLGlot](https://github.com/tobymao/sqlglot) builds the AST. The code in this repository is the part SQLGlot does not give you: scope handling, column resolution, diagnostics and the lineage graph.

## Features

- Output columns classified as `column`, `expression` or `constant`, with the constants they contain.
- Table aliases, qualified and unqualified column names, `JOIN ... USING`, self-joins.
- Optional JSON **schema** to detect unknown columns and ambiguous references.
- Joins, derived-table subqueries, scalar / `IN` / `EXISTS` subqueries (including correlated ones), non-recursive CTEs (also chained, with column lists), `UNION` / `INTERSECT` / `EXCEPT`.
- Directed lineage graph with direct and transitive dependencies, exported as text tree, JSON, Graphviz DOT or Mermaid.
- Diagnostics with line/column and a caret under the problem. A **certain error** (`error`) is distinguished from a case that **cannot be decided** with the information available (`warning`).

## Installation

Requires Python 3.9+ (developed and tested on Python 3.12 with SQLGlot 30.x).

```bash
git clone <your-repository-url> SQLTrace
cd SQLTrace
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
```

## Usage

### Command line

```bash
sqltrace analyze "SELECT prix * quantite AS total FROM commande"
sqltrace analyze -f examples/02_join.sql -s examples/schema.json
sqltrace analyze -f examples/03_cte.sql -s examples/schema.json --format json
sqltrace analyze -f examples/03_cte.sql -s examples/schema.json --format mermaid
sqltrace explain  -f examples/03_cte.sql -s examples/schema.json -c chiffre_affaires
```

Options: `-f/--file` (use `-` for stdin), `-s/--schema`, `-d/--dialect` (any SQLGlot dialect), `--format text|json|dot|mermaid` (analyze).

Exit codes: `0` success (warnings allowed), `1` the query has certain errors, `2` unusable input (syntax error, unsupported statement, unreadable file or schema).

A schema file maps tables to columns (`{"table": {"col": "TYPE"}}` also works; types are ignored):

```json
{ "client": ["id", "nom", "ville"], "commande": ["id", "client_id", "prix", "quantite"] }
```

Multi-level lineage (`examples/03_cte.sql`):

```text
$ sqltrace explain -f examples/03_cte.sql -s examples/schema.json -c chiffre_affaires
query.chiffre_affaires  = SUM(v.total)
└── cte:ventes.total  = prix * quantite
    ├── commande.prix
    └── commande.quantite

Direct dependencies:     cte:ventes.total
Base table dependencies: commande.prix, commande.quantite
```

Diagnostics (`examples/04_errors.sql`):

```text
error[SQT103] line 1, column 8: Column 'id' is ambiguous: it exists in k, c.
  SELECT id, couleur, k.inconnue
         ^^
error[SQT102] line 1, column 12: Column 'couleur' does not exist in any table of this query (available columns: id, nom, ville, client_id, prix, quantite).
  SELECT id, couleur, k.inconnue
             ^^^^^^^
```

### Python API

```python
from sqltrace import analyze

result = analyze(
    "SELECT k.nom, c.prix * c.quantite AS total FROM commande c JOIN client k ON c.client_id = k.id",
    schema={"client": ["id", "nom"], "commande": ["id", "client_id", "prix", "quantite"]},
)
total = result.output("total")
total.base_sources            # ['commande.prix', 'commande.quantite']
result.graph.downstream("commande.prix")   # impact analysis: what depends on this column
result.ok, result.diagnostics
result.to_dict()              # JSON-serialisable
```

Exceptions that stop an analysis: `SQLSyntaxError`, `UnsupportedSQLError`, `SchemaError` (all subclasses of `SQLTraceError`).

## How it works

```text
SQL text ──parser.py──▶ SQLGlot AST ──resolver.py──▶ outputs + LineageGraph ──▶ analyzer.py / report.py / cli.py
                                          │
                                  scopes.py (sources visible in each SELECT)
                                  schema.py (known columns)
```

| Module | Responsibility |
| --- | --- |
| `parser.py` | SQL → AST, syntax errors with positions, rejects non-queries |
| `schema.py` | Case-insensitive table → columns mapping, JSON loading and validation |
| `scopes.py` | `Source` and `Scope`: resolution of one column name against the visible sources |
| `resolver.py` | Walks queries scope by scope, resolves columns, builds the graph |
| `graph.py` | Directed graph, direct/transitive queries, DOT / Mermaid / JSON export |
| `analyzer.py` | Public `analyze()` and result objects |
| `report.py`, `cli.py` | Text rendering and command line |
| `errors.py` | Exceptions, `Diagnostic`, `Position` |

Graph node ids: `table.column` for real table columns, `cte:name.column` and `sub:alias.column` for intermediate outputs, `query.column` for the analysed query's outputs, `?.name` for references that could not be resolved. See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for resolution rules and diagnostic codes.

## Supported and unsupported

Supported: `SELECT` (incl. `DISTINCT`, window functions, `CASE`, functions), `JOIN` of all kinds with `ON` / `USING`, derived tables, expression subqueries, non-recursive CTEs, set operations. Dialect: SQLGlot's generic dialect by default; others are passed to SQLGlot with `--dialect` but are only lightly tested.

Known limitations:

- Only **one `SELECT`-type statement** per run; `INSERT`, `CREATE`, `UPDATE`… are rejected (`SQT002`). `WITH RECURSIVE` is rejected.
- Lineage covers the **select list only**. Columns used in `WHERE`, `JOIN ... ON`, `GROUP BY`, `HAVING`, `ORDER BY` are checked for existence and ambiguity but are not recorded as dependencies of the outputs.
- `SELECT *` needs a schema to be expanded (directly or through a CTE/subquery). Without it, SQLTrace reports `SQT202` and does not guess.
- Without a schema, a column in a multi-table query that is not qualified cannot be attributed (`SQT201` warning). A column is assumed to exist when only one table could provide it.
- `NATURAL JOIN`, table functions (`UNNEST`, `LATERAL`, `VALUES` sources) are flagged (`SQT204`/`SQT205`) and not traced.
- Identifiers are compared case-insensitively; quoting and dialect-specific case rules are not modelled.
- Views, functions' return columns and `SELECT ... INTO` are out of scope.

## Tests

```bash
pytest
```

The suite covers each version of the engine (basics, schema resolution, joins/subqueries/CTEs, graph and exports, CLI exit codes and messages).

## License

MIT — see [LICENSE](LICENSE).
