"""Command line interface behaviour and exit codes."""

import json

from sqltrace.cli import main

SCHEMA = {"commande": ["id", "prix", "quantite"]}


def run(capsys, *argv):
    code = main(list(argv))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def test_analyze_text(capsys):
    code, out, _ = run(capsys, "analyze", "SELECT prix * quantite AS total FROM Commande")
    assert code == 0
    assert "total  [expression]  prix * quantite" in out
    assert "from: commande.prix, commande.quantite" in out


def test_analyze_json(capsys):
    code, out, _ = run(capsys, "analyze", "SELECT prix FROM commande", "--format", "json")
    data = json.loads(out)
    assert code == 0 and data["outputs"][0]["base_sources"] == ["commande.prix"]


def test_analyze_dot_and_mermaid(capsys):
    assert "digraph" in run(capsys, "analyze", "SELECT a FROM t", "--format", "dot")[1]
    assert "flowchart" in run(capsys, "analyze", "SELECT a FROM t", "--format", "mermaid")[1]


def test_query_from_file_and_schema(capsys, tmp_path):
    sql_file = tmp_path / "q.sql"
    sql_file.write_text("SELECT couleur FROM commande", encoding="utf-8")
    schema_file = tmp_path / "s.json"
    schema_file.write_text(json.dumps(SCHEMA), encoding="utf-8")
    code, out, _ = run(capsys, "analyze", "-f", str(sql_file), "-s", str(schema_file))
    assert code == 1  # certain error found
    assert "error[SQT102] line 1, column 8" in out
    assert "^^^^^^^" in out  # caret under the column


def test_warnings_only_exit_zero(capsys):
    code, out, _ = run(capsys, "analyze", "SELECT * FROM t")
    assert code == 0 and "warning[SQT202]" in out


def test_syntax_error_exit_code_and_message(capsys):
    code, out, err = run(capsys, "analyze", "SELECT FROM WHERE x")
    assert code == 2 and out == "" and "error[SQT001] line 1" in err


def test_unsupported_statement(capsys):
    code, _, err = run(capsys, "analyze", "DROP TABLE t")
    assert code == 2 and "SQT002" in err


def test_missing_file_and_missing_schema(capsys, tmp_path):
    assert run(capsys, "analyze", "-f", str(tmp_path / "nope.sql"))[0] == 2
    assert run(capsys, "analyze", "SELECT 1", "-s", str(tmp_path / "nope.json"))[0] == 2


def test_no_query_given(capsys):
    code, _, err = run(capsys, "analyze")
    assert code == 2 and "No query" in err


def test_explain(capsys):
    sql = "WITH c AS (SELECT prix AS p FROM commande) SELECT p * 2 AS d FROM c"
    code, out, _ = run(capsys, "explain", sql, "-c", "d")
    assert code == 0
    assert "query.d" in out and "cte:c.p" in out
    assert "Base table dependencies: commande.prix" in out


def test_explain_unknown_column(capsys):
    code, _, err = run(capsys, "explain", "SELECT a FROM t", "-c", "zzz")
    assert code == 2 and "available: a" in err
