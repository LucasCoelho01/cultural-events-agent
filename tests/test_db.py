import pytest

from cultural_events_agent.config import Settings
from cultural_events_agent.db import (
    RunCounts,
    RunError,
    RunStatus,
    check_schema_contract,
    close_run,
    connect,
    open_run,
    translate_db_errors,
)
from cultural_events_agent.errors import (
    DbContractViolation,
    DbUnavailable,
    SchemaContractMismatch,
)

PASSWORD = "senha-secreta-do-teste"


def make_settings(url: str) -> Settings:
    return Settings(
        database_url=url, anthropic_api_key="sk-teste", user_agent="teste", _env_file=None
    )


@pytest.fixture
def contratos(admin_conn):
    """Permite ao teste trocar as versões de schema_contract e restaura o estado original."""
    original = [r[0] for r in admin_conn.execute("SELECT version FROM schema_contract")]

    def definir(*versions: str) -> None:
        admin_conn.execute("DELETE FROM schema_contract")
        for version in versions:
            admin_conn.execute("INSERT INTO schema_contract (version) VALUES (%s)", (version,))

    yield definir
    admin_conn.execute("DELETE FROM schema_contract")
    for version in original:
        admin_conn.execute("INSERT INTO schema_contract (version) VALUES (%s)", (version,))


@pytest.fixture
def run_ids(admin_conn):
    """Remove as execuções criadas pelo teste."""
    before = admin_conn.execute("SELECT coalesce(max(id), 0) FROM ingestion_run").fetchone()[0]
    yield
    admin_conn.execute("DELETE FROM ingestion_run WHERE id > %s", (before,))


# --- 3.2 conexão -----------------------------------------------------------------------------


def test_connect_com_credenciais_validas(postgres):
    url = (
        f"postgresql://{postgres['user']}:{postgres['password']}"
        f"@{postgres['host']}:{postgres['port']}/{postgres['dbname']}"
    )
    with connect(make_settings(url)) as conn:
        assert conn.execute("SELECT 1").fetchone()[0] == 1


def test_connect_porta_fechada_vira_db_unavailable():
    url = f"postgresql://agent_writer:{PASSWORD}@127.0.0.1:1/cultural_events"
    with pytest.raises(DbUnavailable) as info:
        connect(make_settings(url), timeout=2)
    assert info.value.code == "DB_UNAVAILABLE"
    assert PASSWORD not in str(info.value)


def test_connect_senha_errada_vira_db_unavailable(postgres):
    url = (
        f"postgresql://{postgres['user']}:{PASSWORD}"
        f"@{postgres['host']}:{postgres['port']}/{postgres['dbname']}"
    )
    with pytest.raises(DbUnavailable) as info:
        connect(make_settings(url))
    assert PASSWORD not in str(info.value)


# --- 3.3 contrato ----------------------------------------------------------------------------


def test_contrato_1_0_ok(agent_conn):
    assert check_schema_contract(agent_conn) == "1.0"


def test_contrato_minor_maior_ok(agent_conn, contratos):
    contratos("1.0", "1.3")
    assert check_schema_contract(agent_conn) == "1.3"


def test_contrato_2_0_recusado(agent_conn, contratos):
    contratos("1.0", "2.0")
    with pytest.raises(SchemaContractMismatch) as info:
        check_schema_contract(agent_conn)
    assert info.value.code == "SCHEMA_CONTRACT_MISMATCH"
    assert "2.0" in info.value.message
    assert "1.x" in info.value.message


def test_contrato_ordenacao_numerica(agent_conn, contratos):
    contratos("1.0", "10.0")
    with pytest.raises(SchemaContractMismatch) as info:
        check_schema_contract(agent_conn)
    assert "10.0" in info.value.message


def test_contrato_tabela_vazia_recusada(agent_conn, contratos):
    contratos()
    with pytest.raises(SchemaContractMismatch):
        check_schema_contract(agent_conn)


def test_contrato_nao_grava_nada(agent_conn, contratos, admin_conn):
    contratos("2.0")
    before = admin_conn.execute("SELECT count(*) FROM ingestion_run").fetchone()[0]
    with pytest.raises(SchemaContractMismatch):
        check_schema_contract(agent_conn)
    assert admin_conn.execute("SELECT count(*) FROM ingestion_run").fetchone()[0] == before


# --- 3.4 ingestion_run -----------------------------------------------------------------------


def read_run(admin_conn, run_id):
    return admin_conn.execute(
        "SELECT status, finished_at, created, updated, unchanged, failed, errors"
        " FROM ingestion_run WHERE id = %s",
        (run_id,),
    ).fetchone()


def test_abrir_execucao(agent_conn, admin_conn, source_id, run_ids):
    run_id = open_run(agent_conn, source_id)
    status, finished_at, created, updated, unchanged, failed, errors = read_run(admin_conn, run_id)
    assert status == "RUNNING"
    assert finished_at is None
    assert (created, updated, unchanged, failed) == (0, 0, 0, 0)
    assert errors == []


def test_abrir_com_fonte_inexistente(agent_conn, admin_conn, source_id, run_ids):
    before = admin_conn.execute("SELECT count(*) FROM ingestion_run").fetchone()[0]
    with pytest.raises(DbContractViolation) as info:
        open_run(agent_conn, 999_999)
    assert info.value.code == "DB_CONTRACT_VIOLATION"
    assert admin_conn.execute("SELECT count(*) FROM ingestion_run").fetchone()[0] == before


def test_fechar_como_partial_com_erro(agent_conn, admin_conn, source_id, run_ids):
    run_id = open_run(agent_conn, source_id)
    close_run(
        agent_conn,
        run_id,
        RunStatus.PARTIAL,
        RunCounts(created=2, updated=1, unchanged=3, failed=1),
        [RunError(external_id="abc-1", message="data inválida")],
    )
    status, finished_at, created, updated, unchanged, failed, errors = read_run(admin_conn, run_id)
    assert status == "PARTIAL"
    assert finished_at is not None
    assert (created, updated, unchanged, failed) == (2, 1, 3, 1)
    assert errors == [{"external_id": "abc-1", "message": "data inválida"}]


@pytest.mark.parametrize("status", ["RUNNING", "DONE", ""])
def test_fechar_com_status_invalido(agent_conn, admin_conn, source_id, run_ids, status):
    run_id = open_run(agent_conn, source_id)
    with pytest.raises(DbContractViolation):
        close_run(agent_conn, run_id, status, RunCounts())
    assert read_run(admin_conn, run_id)[0] == "RUNNING"


def test_fechar_com_contador_negativo(agent_conn, admin_conn, source_id, run_ids):
    run_id = open_run(agent_conn, source_id)
    with pytest.raises(DbContractViolation):
        close_run(agent_conn, run_id, RunStatus.SUCCESS, RunCounts.model_construct(created=-1))
    assert read_run(admin_conn, run_id)[0] == "RUNNING"


def test_fechamento_repetido(agent_conn, admin_conn, source_id, run_ids):
    run_id = open_run(agent_conn, source_id)
    close_run(agent_conn, run_id, RunStatus.SUCCESS, RunCounts(created=5))
    with pytest.raises(DbContractViolation):
        close_run(agent_conn, run_id, RunStatus.FAILED, RunCounts(failed=9))
    status, _, created, _, _, failed, _ = read_run(admin_conn, run_id)
    assert (status, created, failed) == ("SUCCESS", 5, 0)


def test_fechar_execucao_inexistente(agent_conn):
    with pytest.raises(DbContractViolation):
        close_run(agent_conn, 999_999, RunStatus.SUCCESS, RunCounts())


# --- 3.5 parametrização e permissões ---------------------------------------------------------


def test_mensagem_com_sql_e_gravada_literalmente(agent_conn, admin_conn, source_id, run_ids):
    malicioso = "'; DROP TABLE source; --"
    run_id = open_run(agent_conn, source_id)
    close_run(
        agent_conn,
        run_id,
        RunStatus.FAILED,
        RunCounts(failed=1),
        [RunError(external_id=malicioso, message=malicioso)],
    )
    errors = read_run(admin_conn, run_id)[6]
    assert errors == [{"external_id": malicioso, "message": malicioso}]
    assert admin_conn.execute("SELECT count(*) FROM source").fetchone()[0] >= 1


def test_escrita_em_schema_contract_negada(agent_conn):
    with pytest.raises(DbContractViolation) as info:
        with translate_db_errors():
            agent_conn.execute("INSERT INTO schema_contract (version) VALUES ('9.9')")
    assert info.value.code == "DB_CONTRACT_VIOLATION"
    assert "42501" in info.value.message
