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
