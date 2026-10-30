import json

import pytest

from cultural_events_agent import cli
from cultural_events_agent.errors import DbUnavailable, LlmUnavailable, SchemaContractMismatch
from cultural_events_agent.llm import FakeLlm

API_KEY = "sk-ant-chave-secreta-1234"
DB_PASSWORD = "senha-do-banco-9876"
DB_URL = f"postgresql://agent_writer:{DB_PASSWORD}@localhost:5432/cultural_events"


class FakeConn:
    closed_by_cli = False

    def close(self):
        self.closed_by_cli = True


@pytest.fixture(autouse=True)
def ambiente(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)  # sem .env no diretório
    monkeypatch.setenv("DATABASE_URL", DB_URL)
    monkeypatch.setenv("ANTHROPIC_API_KEY", API_KEY)
    monkeypatch.setenv("USER_AGENT", "teste")


@pytest.fixture
def tudo_ok(monkeypatch):
    """Banco, contrato e modelo falsos que funcionam; devolve o que foi chamado."""
    calls = {"connect": 0, "llm": FakeLlm()}
    conn = FakeConn()

    def fake_connect(settings):
        calls["connect"] += 1
        return conn

    monkeypatch.setattr(cli, "connect", fake_connect)
    monkeypatch.setattr(cli, "check_schema_contract", lambda c: "1.0")
    monkeypatch.setattr(cli, "build_llm", lambda settings: calls["llm"])
    calls["conn"] = conn
    return calls


def log_entries(capsys) -> list[dict]:
    err = capsys.readouterr().err
    return [json.loads(line) for line in err.splitlines() if line.strip()]


def test_check_ok(tudo_ok, capsys):
    assert cli.main(["check"]) == 0
    events = [e["event"] for e in log_entries(capsys)]
    assert events == ["config_ok", "db_connected", "schema_contract_ok", "llm_ok", "check_ok"]
    assert len(tudo_ok["llm"].prompts) == 1
    assert tudo_ok["conn"].closed_by_cli


def test_skip_llm_nao_chama_o_modelo(tudo_ok, capsys):
    assert cli.main(["check", "--skip-llm"]) == 0
    assert tudo_ok["llm"].prompts == []
    assert "llm_skipped" in [e["event"] for e in log_entries(capsys)]


def test_config_invalida_sai_com_2_e_nao_acessa_o_banco(tudo_ok, monkeypatch, capsys):
    monkeypatch.delenv("DATABASE_URL")
    assert cli.main(["check"]) == 2
    assert tudo_ok["connect"] == 0
    assert tudo_ok["llm"].prompts == []
    failure = log_entries(capsys)[-1]
    assert failure["error_code"] == "CONFIG_INVALID"
    assert "DATABASE_URL" in failure["detail"]


def test_banco_inacessivel_sai_com_3(tudo_ok, monkeypatch, capsys):
    def fail(settings):
        raise DbUnavailable("banco fora")

    monkeypatch.setattr(cli, "connect", fail)
    assert cli.main(["check"]) == 3
    assert tudo_ok["llm"].prompts == []
    assert log_entries(capsys)[-1]["error_code"] == "DB_UNAVAILABLE"


def test_contrato_incompativel_sai_com_4_e_fecha_conexao(tudo_ok, monkeypatch, capsys):
    def mismatch(conn):
        raise SchemaContractMismatch("contrato 2.0 incompatível; esperado 1.x")

    monkeypatch.setattr(cli, "check_schema_contract", mismatch)
    assert cli.main(["check"]) == 4
    assert tudo_ok["conn"].closed_by_cli
    assert tudo_ok["llm"].prompts == []
    failure = log_entries(capsys)[-1]
    assert failure["level"] == "ERROR"
    assert failure["error_code"] == "SCHEMA_CONTRACT_MISMATCH"


def test_modelo_indisponivel_sai_com_5(tudo_ok, capsys):
    tudo_ok["llm"].error = LlmUnavailable("AuthenticationError")
    assert cli.main(["check"]) == 5
    assert log_entries(capsys)[-1]["error_code"] == "LLM_UNAVAILABLE"


def test_erro_inesperado_sai_com_1_sem_vazar_segredo(tudo_ok, monkeypatch, capsys):
    def boom(conn):
        raise RuntimeError(f"estourou com {API_KEY} e {DB_PASSWORD}")

    monkeypatch.setattr(cli, "check_schema_contract", boom)
    assert cli.main(["check"]) == 1
    raw = capsys.readouterr().err
    assert API_KEY not in raw
    assert DB_PASSWORD not in raw
    failure = json.loads(raw.splitlines()[-1])
    assert failure["error_type"] == "RuntimeError"
    assert failure["error_code"] == "UNEXPECTED_ERROR"


def test_logs_de_falha_nao_vazam_segredos(tudo_ok, monkeypatch, capsys):
    def fail(settings):
        raise DbUnavailable(f"falhou {DB_PASSWORD} e {API_KEY}")

    monkeypatch.setattr(cli, "connect", fail)
    cli.main(["check"])
    raw = capsys.readouterr().err
    assert DB_PASSWORD not in raw
    assert API_KEY not in raw


def test_uso_incorreto_nao_usa_o_codigo_2(capsys):
    with pytest.raises(SystemExit) as info:
        cli.main([])
    assert info.value.code == cli.EXIT_USAGE


def test_check_contra_banco_real(postgres, monkeypatch, capsys):
    url = (
        f"postgresql://{postgres['user']}:{postgres['password']}"
        f"@{postgres['host']}:{postgres['port']}/{postgres['dbname']}"
    )
    monkeypatch.setenv("DATABASE_URL", url)
    assert cli.main(["check", "--skip-llm"]) == 0
    entries = log_entries(capsys)
    contrato = next(e for e in entries if e["event"] == "schema_contract_ok")
    assert contrato["version"] == "1.0"
    conectado = next(e for e in entries if e["event"] == "db_connected")
    assert f"{postgres['user']}:***@" in conectado["database"]
    assert any(e["event"] == "config_weak_secret" for e in entries)  # senha do contêiner é curta


def test_segredo_fraco_emite_aviso_sem_o_valor(tudo_ok, monkeypatch, capsys):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "abc")
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:curta@localhost/db")
    assert cli.main(["check", "--skip-llm"]) == 0
    raw = capsys.readouterr().err
    warnings = [json.loads(line) for line in raw.splitlines() if '"WARNING"' in line]
    assert [(w["event"], w["error_code"], w["variable"]) for w in warnings] == [
        ("config_weak_secret", "CONFIG_WEAK_SECRET", "ANTHROPIC_API_KEY"),
        ("config_weak_secret", "CONFIG_WEAK_SECRET", "DATABASE_URL"),
    ]
    assert '"abc"' not in raw
    assert "curta" not in raw


def test_segredos_fortes_nao_emitem_aviso(tudo_ok, capsys):
    assert cli.main(["check", "--skip-llm"]) == 0
    assert "CONFIG_WEAK_SECRET" not in capsys.readouterr().err
