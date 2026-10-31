import json
from pathlib import Path

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
    assert failure["detail"].startswith("estourou com ")  # a mensagem agora aparece, mascarada
    assert "***" in failure["detail"]
    assert "traceback" not in failure


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


# ---- comando run ----

RUN_ARGS = ["run", "--source", "sympla", "--city", "sp/sao-paulo"]


class FetcherFalso:
    fechado = False

    def close(self):
        self.fechado = True


@pytest.fixture
def run_ok(tudo_ok, monkeypatch):
    """`run_ingestion` falso: registra os argumentos e devolve ou levanta o que o teste definir."""
    from cultural_events_agent.db import RunCounts, RunStatus
    from cultural_events_agent.graph import RunResult

    estado = {
        "kwargs": None,
        "fetcher": FetcherFalso(),
        "result": RunResult(
            run_id=7,
            status=RunStatus.SUCCESS,
            counts=RunCounts(created=2, updated=1, unchanged=4, failed=0),
            skipped={"TESTE_EVENT": 2},
        ),
        "error": None,
    }

    def fake_run(conn, settings, source, llm, fetcher, **kwargs):
        estado["kwargs"] = {"conn": conn, "source": source, "fetcher": fetcher, **kwargs}
        if estado["error"]:
            raise estado["error"]
        return estado["result"]

    monkeypatch.setattr(cli, "run_ingestion", fake_run)
    monkeypatch.setattr(cli, "build_fetcher", lambda settings: estado["fetcher"])
    estado.update(tudo_ok)
    return estado


def test_run_normal_repassa_os_argumentos(run_ok, capsys):
    assert cli.main([*RUN_ARGS, "--limit", "10"]) == 0
    kwargs = run_ok["kwargs"]
    assert (kwargs["state_uf"], kwargs["city_slug"]) == ("sp", "sao-paulo")
    assert kwargs["limit"] == 10 and kwargs["dry_run"] is False
    assert kwargs["debug_dir"] is None
    assert kwargs["conn"].autocommit is True
    assert run_ok["fetcher"].fechado and run_ok["conn"].closed_by_cli


def test_run_dry_run_repassa_a_flag(run_ok):
    assert cli.main([*RUN_ARGS, "--dry-run"]) == 0
    assert run_ok["kwargs"]["dry_run"] is True
    assert run_ok["kwargs"]["limit"] is None


def test_run_com_log_level_debug_repassa_o_diretorio_de_debug(run_ok, monkeypatch):
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")
    assert cli.main(RUN_ARGS) == 0
    assert run_ok["kwargs"]["debug_dir"] == Path(".debug")


def test_run_com_falhas_parciais_sai_com_0(run_ok):
    from cultural_events_agent.db import RunCounts, RunStatus
    from cultural_events_agent.graph import RunResult

    run_ok["result"] = RunResult(
        run_id=8, status=RunStatus.PARTIAL, counts=RunCounts(created=1, failed=2)
    )
    assert cli.main(RUN_ARGS) == 0


@pytest.mark.parametrize(
    ("argv", "motivo"),
    [
        (["run", "--source", "inexistente", "--city", "sp/sao-paulo"], "fonte"),
        (["run", "--source", "sympla", "--city", "sao-paulo"], "cidade sem barra"),
        (["run", "--source", "sympla", "--city", "sao/paulo/x"], "cidade com barras demais"),
        (["run", "--source", "sympla", "--city", "sp/sao-paulo", "--limit", "0"], "limit zero"),
        (["run", "--source", "sympla", "--city", "sp/sao-paulo", "--limit", "x"], "limit texto"),
        (["run", "--city", "sp/sao-paulo"], "sem fonte"),
    ],
)
def test_run_uso_incorreto_sai_com_64(argv, motivo, capsys):
    with pytest.raises(SystemExit) as info:
        cli.main(argv)
    assert info.value.code == cli.EXIT_USAGE, motivo


@pytest.mark.parametrize(
    ("erro", "codigo", "saida"),
    [
        ("CityNotFound", "CITY_NOT_FOUND", 6),
        ("CityInactive", "CITY_INACTIVE", 7),
        ("SourceNotFound", "SOURCE_NOT_FOUND", 8),
        ("RobotsDisallowed", "ROBOTS_DISALLOWED", 9),
        ("FetchFailed", "FETCH_FAILED", 10),
        ("RateLimited", "RATE_LIMITED", 11),
        ("LlmUnavailable", "LLM_UNAVAILABLE", 5),
    ],
)
def test_run_codigos_de_saida(run_ok, capsys, erro, codigo, saida):
    from cultural_events_agent import errors

    run_ok["error"] = getattr(errors, erro)("falhou")
    assert cli.main(RUN_ARGS) == saida
    falha = log_entries(capsys)[-1]
    assert falha["event"] == "run_failed" and falha["error_code"] == codigo
    assert run_ok["fetcher"].fechado and run_ok["conn"].closed_by_cli


def test_run_cidade_inexistente_contra_banco_real(postgres, clean_db, monkeypatch, capsys):
    monkeypatch.setattr(cli, "build_fetcher", lambda settings: FetcherFalso())
    url = (
        f"postgresql://{postgres['user']}:{postgres['password']}"
        f"@{postgres['host']}:{postgres['port']}/{postgres['dbname']}"
    )
    monkeypatch.setenv("DATABASE_URL", url)
    assert cli.main(["run", "--source", "sympla", "--city", "xx/lugar-nenhum"]) == 6
    assert log_entries(capsys)[-1]["error_code"] == "CITY_NOT_FOUND"


def test_run_cidade_inativa_contra_banco_real(postgres, inactive_city_id, monkeypatch, capsys):
    monkeypatch.setattr(cli, "build_fetcher", lambda settings: FetcherFalso())
    url = (
        f"postgresql://{postgres['user']}:{postgres['password']}"
        f"@{postgres['host']}:{postgres['port']}/{postgres['dbname']}"
    )
    monkeypatch.setenv("DATABASE_URL", url)
    assert cli.main(["run", "--source", "sympla", "--city", "rj/rio-de-janeiro"]) == 7
    assert log_entries(capsys)[-1]["error_code"] == "CITY_INACTIVE"


def test_erro_inesperado_com_log_level_debug_traz_o_traceback_sem_segredos(
    tudo_ok, monkeypatch, capsys
):
    def boom(conn):
        raise RuntimeError(f"estourou com {API_KEY}")

    monkeypatch.setenv("LOG_LEVEL", "DEBUG")
    monkeypatch.setattr(cli, "check_schema_contract", boom)
    assert cli.main(["check"]) == 1
    raw = capsys.readouterr().err
    assert API_KEY not in raw
    failure = json.loads(raw.splitlines()[-1])
    assert "Traceback (most recent call last)" in failure["traceback"]
    assert "RuntimeError" in failure["traceback"] and "***" in failure["traceback"]


def test_log_level_invalido_sai_com_2(tudo_ok, monkeypatch, capsys):
    monkeypatch.setenv("LOG_LEVEL", "barulhento")
    assert cli.main(["check"]) == 2
    assert "LOG_LEVEL" in log_entries(capsys)[-1]["detail"]


def _banco_real(postgres, monkeypatch):
    url = (
        f"postgresql://{postgres['user']}:{postgres['password']}"
        f"@{postgres['host']}:{postgres['port']}/{postgres['dbname']}"
    )
    monkeypatch.setenv("DATABASE_URL", url)


def _fluxo_falso(monkeypatch):
    from test_graph import FakeSource, fake_fetcher

    monkeypatch.setattr(cli, "SOURCES", {"sympla": FakeSource})
    fetcher = fake_fetcher(3)
    fetcher.listings = {k.replace("-SP?", "-sp?"): v for k, v in fetcher.listings.items()}
    monkeypatch.setattr(cli, "build_fetcher", lambda settings: fetcher)
    monkeypatch.setattr(cli, "build_llm", lambda settings: FakeLlm())


def test_run_de_ponta_a_ponta_emite_run_finished(postgres, city_id, monkeypatch, capsys):
    _banco_real(postgres, monkeypatch)
    _fluxo_falso(monkeypatch)
    assert cli.main(RUN_ARGS) == 0
    final = log_entries(capsys)[-1]
    assert final["event"] == "run_finished" and final["dry_run"] is False
    assert final["status"] == "SUCCESS" and isinstance(final["run_id"], int)
    assert (final["items_discovered"], final["items_created"], final["items_pending"]) == (3, 3, 0)


def test_run_dry_run_de_ponta_a_ponta_emite_run_finished(postgres, city_id, monkeypatch, capsys):
    _banco_real(postgres, monkeypatch)
    _fluxo_falso(monkeypatch)
    assert cli.main([*RUN_ARGS, "--dry-run"]) == 0
    captured = capsys.readouterr()
    linhas = [json.loads(linha) for linha in captured.out.splitlines()]
    assert [linha["action"] for linha in linhas] == ["create"] * 3
    final = [json.loads(x) for x in captured.err.splitlines() if x.strip()][-1]
    assert final["event"] == "run_finished" and final["dry_run"] is True
    assert final["run_id"] == "-" and final["items_created"] == 3


def test_run_abortado_tambem_emite_run_finished(postgres, clean_db, monkeypatch, capsys):
    _banco_real(postgres, monkeypatch)
    _fluxo_falso(monkeypatch)
    assert cli.main(["run", "--source", "sympla", "--city", "xx/lugar-nenhum"]) == 6
    entries = log_entries(capsys)
    assert [e["event"] for e in entries][-2:] == ["run_finished", "run_failed"]
    assert entries[-2]["status"] == "FAILED" and entries[-2]["error_code"] == "CITY_NOT_FOUND"
