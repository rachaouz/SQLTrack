# Architecture notes

## Pipeline

1. `parse_sql` turns the text into one SQLGlot query expression (`Select`, `Union`…). Anything else is rejected.
2. `Resolver` resolves the top-level query. For each query it:
   1. registers its CTEs (in order; each CTE can use earlier ones),
   2. builds a `Scope` from `FROM`/`JOIN` items (tables, CTE references, derived tables),
   3. resolves each select-list item into an `Output` (the node ids it depends on, its constants, its kind),
   4. checks the other clauses (`ON`, `WHERE`, `GROUP BY`, `HAVING`, `QUALIFY`, `ORDER BY`) for diagnostics only,
   5. gives every output a graph node (`scope_id.column`) and edges from its dependencies.
3. `analyze` wraps outputs, graph and diagnostics in an `AnalysisResult`.

A derived table or CTE is resolved *before* the query that uses it, so the outer query sees its columns as ordinary sources whose node ids point at the inner outputs. This is how dependencies propagate through any number of levels.

## Resolution rules (`scopes.py`)

Qualified `t.c`: look for a source named `t` in the current scope, then in enclosing scopes (correlated subqueries). No such source → `SQT101`. Source with known columns that lacks `c` → `SQT102`. Source with unknown columns → accepted (the qualifier makes the origin certain).

Unqualified `c`, per scope from the innermost outwards:

| Situation | Result |
| --- | --- |
| 2+ sources with known columns have `c` (and `c` is not a `USING` column) | `SQT103` ambiguous (certain) |
| 2+ sources have `c`, joined with `USING (c)` | resolved to all of them |
| exactly 1 known source has `c`, no source with unknown columns | resolved |
| 1 known source has `c` but some source has unknown columns | `SQT201` undecidable |
| no known source has `c`, exactly 1 source with unknown columns | resolved to it (the others are known not to have it) |
| no known source has `c`, 2+ sources with unknown columns | `SQT201` undecidable |
| nobody has `c` in this scope | try the enclosing scope; none left → `SQT102` |

`GROUP BY`, `HAVING`, `QUALIFY` and `ORDER BY` may use an output alias; that is not an error.

## Diagnostic codes

| Code | Severity | Meaning |
| --- | --- | --- |
| SQT001 | error (stops) | Syntax error |
| SQT002 | error (stops) | Unsupported statement or construct |
| SQT003 | error (stops) | Invalid schema |
| SQT101 | error | Unknown table or alias used as qualifier |
| SQT102 | error | Column does not exist |
| SQT103 | error | Ambiguous column |
| SQT104 | error | Duplicate table name/alias in one `FROM` |
| SQT105 | error | Set operation branches have different column counts |
| SQT201 | warning | Column origin cannot be determined |
| SQT202 | warning | `*` cannot be expanded (unknown columns) |
| SQT203 | warning | Table not present in the provided schema |
| SQT204 | warning | Unsupported source (table function, `UNNEST`, …) |
| SQT205 | warning | `NATURAL JOIN` not traced |

"error" means certain given the query and schema; "warning" means the available information does not allow a verdict.

## Design choices

- No graph library: the graph is a few dozen lines and DOT/Mermaid are text exports, so the only runtime dependency is SQLGlot.
- Positions come from the identifier offsets SQLGlot records; they are converted to line/column against the original text.
- Checks on non-select clauses never add nodes to the graph, so subqueries in `WHERE` do not pollute the lineage.
