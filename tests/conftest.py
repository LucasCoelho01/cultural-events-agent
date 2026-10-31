import os
import re
from collections.abc import Iterator
from pathlib import Path

import psycopg
import pytest

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_MIGRATIONS_DIR = "../cultural-events-app/database/migrations"
MIGRATION_NAME = re.compile(r"^V(\d+)__.*\.sql$")


def migrations_dir() -> Path:
    raw = os.environ.get("MIGRATIONS_DIR", DEFAULT_MIGRATIONS_DIR)
    path = Path(raw)
    return path if path.is_absolute() else (ROOT / path).resolve()


def sorted_migrations(directory: Path) -> list[Path]:
    """Os .sql em ordem numérica do prefixo V<n> (V2 antes de V10)."""
    found = [
        (int(m.group(1)), p) for p in directory.glob("*.sql") if (m := MIGRATION_NAME.match(p.name))
    ]
    return [path for _, path in sorted(found)]


@pytest.fixture(scope="session")
def migrations() -> list[Path]:
    directory = migrations_dir()
    if not directory.is_dir():
        pytest.skip(
            f"Diretório de migrations não encontrado: {directory}. Clone o cultural-events-app "
            f"ao lado deste repo ou defina MIGRATIONS_DIR com a pasta das migrations .sql."
        )
    files = sorted_migrations(directory)
    if not files:
        pytest.skip(f"Nenhuma migration V<n>__*.sql em {directory} (veja MIGRATIONS_DIR).")
    return files


@pytest.fixture(scope="session")
def postgres(migrations) -> Iterator[dict[str, str | int]]:
    """Postgres 16 descartável com as migrations do cultural-events-app aplicadas."""
    from testcontainers.community.postgres import PostgresContainer

    try:
        container = PostgresContainer("postgres:16", driver=None)
        container.start()
    except Exception as exc:  # Docker ausente ou parado
        pytest.skip(f"Não foi possível iniciar o Postgres via Docker/Testcontainers: {exc}")
    try:
        info = {
            "host": container.get_container_host_ip(),
            "port": int(container.get_exposed_port(5432)),
            "user": container.username,
            "password": container.password,
            "dbname": container.dbname,
        }
        with psycopg.connect(**info, autocommit=True) as conn:
            for path in migrations:
                conn.execute(path.read_text(encoding="utf-8"))
        yield info
    finally:
        container.stop()


@pytest.fixture
def admin_conn(postgres) -> Iterator[psycopg.Connection]:
    """Conexão do administrador do contêiner (autocommit), para preparar e limpar dados."""
    with psycopg.connect(**postgres, autocommit=True) as conn:
        yield conn


@pytest.fixture
def agent_conn(postgres) -> Iterator[psycopg.Connection]:
    """Conexão que roda como agent_writer via SET ROLE (sem autocommit)."""
    with psycopg.connect(**postgres) as conn:
        conn.execute("SET ROLE agent_writer")
        conn.commit()
        yield conn
        conn.rollback()


@pytest.fixture
def source_id(admin_conn) -> int:
    return admin_conn.execute("SELECT id FROM source WHERE code = 'sympla'").fetchone()[0]


pytest_plugins = ["pytester"]


@pytest.fixture
def make_settings():
    """Fábrica de Settings de teste, sem ler .env."""
    from cultural_events_agent.config import Settings

    def factory(**extra) -> Settings:
        values = {
            "database_url": "postgresql://u:senha-do-banco-1234@localhost/db",
            "anthropic_api_key": "sk-ant-chave-secreta-1234",
            "user_agent": "cultural-events-agent/0.1 (teste@example.com)",
            "request_delay_seconds": 2,
        }
        values.update(extra)
        return Settings(_env_file=None, **values)

    return factory


FIXTURES = ROOT / "tests" / "fixtures" / "sympla"


@pytest.fixture
def sympla_html():
    """Lê uma fixture HTML da Sympla pelo nome do arquivo."""

    def read(name: str) -> str:
        return (FIXTURES / name).read_text(encoding="utf-8")

    return read


@pytest.fixture
def writer_conn(postgres) -> Iterator[psycopg.Connection]:
    """agent_writer em autocommit: cada `conn.transaction()` é um BEGIN/COMMIT de verdade."""
    with psycopg.connect(**postgres, autocommit=True) as conn:
        conn.execute("SET ROLE agent_writer")
        yield conn


_CLEAN_SQL = [
    "DELETE FROM event_source_link",
    "DELETE FROM occurrence",
    "DELETE FROM event",
    "DELETE FROM venue",
    "DELETE FROM ingestion_run",
    "DELETE FROM source WHERE code <> 'sympla'",
    "DELETE FROM city WHERE slug = 'rio-de-janeiro'",
]


@pytest.fixture
def clean_db(admin_conn):
    """Zera os dados de eventos antes e depois (mantém source 'sympla', cidades e categorias)."""
    for sql in _CLEAN_SQL:
        admin_conn.execute(sql)
    yield
    for sql in _CLEAN_SQL:
        admin_conn.execute(sql)


@pytest.fixture
def city_id(admin_conn, clean_db) -> int:
    return admin_conn.execute(
        "SELECT id FROM city WHERE state_uf = 'SP' AND slug = 'sao-paulo'"
    ).fetchone()[0]


@pytest.fixture
def inactive_city_id(admin_conn, clean_db) -> int:
    return admin_conn.execute(
        "INSERT INTO city (ibge_code, state_uf, name, slug, timezone, active) "
        "VALUES (3304557, 'RJ', 'Rio de Janeiro', 'rio-de-janeiro', 'America/Sao_Paulo', false) "
        "RETURNING id"
    ).fetchone()[0]
