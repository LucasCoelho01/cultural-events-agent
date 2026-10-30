from pathlib import Path

import conftest


def test_migrations_aplicadas_e_contrato_1_0(agent_conn):
    row = agent_conn.execute("SELECT version FROM schema_contract").fetchone()
    assert row[0] == "1.0"
    assert agent_conn.execute("SELECT current_user").fetchone()[0] == "agent_writer"


def test_migrations_em_ordem_numerica(tmp_path):
    for name in ["V10__j.sql", "V2__b.sql", "V1__a.sql", "notas.txt", "V3__c.sql"]:
        (tmp_path / name).write_text("SELECT 1;")
    assert [p.name for p in conftest.sorted_migrations(tmp_path)] == [
        "V1__a.sql",
        "V2__b.sql",
        "V3__c.sql",
        "V10__j.sql",
    ]


def test_pula_com_mensagem_clara_sem_diretorio(pytester, monkeypatch):
    pytester.makeconftest(Path(conftest.__file__).read_text(encoding="utf-8"))
    pytester.makepyfile("def test_x(migrations):\n    pass\n")
    monkeypatch.setenv("MIGRATIONS_DIR", str(pytester.path / "nao-existe"))
    result = pytester.runpytest("-rs")
    result.assert_outcomes(skipped=1)
    result.stdout.fnmatch_lines(["*Diretório de migrations não encontrado*MIGRATIONS_DIR*"])
