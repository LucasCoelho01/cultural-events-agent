"""Acesso ao Postgres do cultural-events-app, conforme database/AGENT_CONTRACT.md (contrato 1.0).

Todo SQL aqui usa placeholders; nenhum valor é concatenado no texto da instrução.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from enum import StrEnum

import psycopg
from psycopg import errors as pg_errors
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field, ValidationError

from cultural_events_agent.config import Settings
from cultural_events_agent.errors import (
    DbContractViolation,
    DbUnavailable,
    SchemaContractMismatch,
)

SUPPORTED_MAJOR = 1

# Seção 2 do contrato: a maior versão, comparada numericamente por componente.
_LATEST_CONTRACT_SQL = """
    SELECT version FROM schema_contract
    ORDER BY string_to_array(version, '.')::int[] DESC LIMIT 1
"""

# Seção 3 do contrato: uma linha por execução e por fonte.
_OPEN_RUN_SQL = "INSERT INTO ingestion_run (source_id) VALUES (%s) RETURNING id"

# Fecha uma única vez: só uma execução ainda em RUNNING pode ser fechada.
_CLOSE_RUN_SQL = """
    UPDATE ingestion_run
       SET status = %s,
           finished_at = now(),
           created = %s, updated = %s, unchanged = %s, failed = %s,
           errors = %s
     WHERE id = %s AND status = 'RUNNING'
"""


class RunStatus(StrEnum):
    """Estados finais de uma execução (RUNNING só existe ao abrir)."""

    SUCCESS = "SUCCESS"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"


class RunCounts(BaseModel):
    created: int = Field(default=0, ge=0)
    updated: int = Field(default=0, ge=0)
    unchanged: int = Field(default=0, ge=0)
    failed: int = Field(default=0, ge=0)


class RunError(BaseModel):
    external_id: str
    message: str


def _scrub(text: str, settings: Settings | None) -> str:
    if settings is not None:
        for secret in settings.secret_values():
            text = text.replace(secret, "***")
    return text


@contextmanager
def translate_db_errors() -> Iterator[None]:
    """Traduz erros do psycopg para os erros do agente, sem expor dados sensíveis."""
    try:
        yield
    except (
        pg_errors.ForeignKeyViolation,
        pg_errors.CheckViolation,
        pg_errors.InsufficientPrivilege,
        pg_errors.NotNullViolation,
        pg_errors.UniqueViolation,
        pg_errors.UndefinedTable,
        pg_errors.UndefinedColumn,
    ) as exc:
        raise DbContractViolation(
            f"o banco recusou a operação (SQLSTATE {exc.sqlstate}): {exc.diag.message_primary}"
        ) from None
    except psycopg.OperationalError as exc:
        raise DbUnavailable(f"banco indisponível: {type(exc).__name__}") from None


def connect(settings: Settings, timeout: int = 10) -> psycopg.Connection:
    """Abre a conexão com o banco; falha de rede ou de autenticação vira DB_UNAVAILABLE."""
    try:
        return psycopg.connect(settings.database_url.get_secret_value(), connect_timeout=timeout)
    except psycopg.Error as exc:
        reason = _scrub(str(exc).strip().splitlines()[0] if str(exc).strip() else "", settings)
        raise DbUnavailable(
            f"não foi possível conectar a {settings.database_url_masked}"
            + (f": {reason}" if reason else "")
        ) from None


def check_schema_contract(conn: psycopg.Connection) -> str:
    """Confere o contrato na partida e devolve a versão lida; MAJOR diferente de 1 é recusado."""
    try:
        row = conn.execute(_LATEST_CONTRACT_SQL).fetchone()
    except pg_errors.UndefinedTable:
        conn.rollback()
        raise SchemaContractMismatch(
            f"a tabela schema_contract não existe; esperado contrato {SUPPORTED_MAJOR}.x"
        ) from None
    except psycopg.OperationalError as exc:
        raise DbUnavailable(f"banco indisponível: {type(exc).__name__}") from None
    finally:
        if not conn.closed and conn.info.transaction_status != psycopg.pq.TransactionStatus.IDLE:
            conn.rollback()
    if row is None:
        raise SchemaContractMismatch(
            f"schema_contract está vazia; esperado contrato {SUPPORTED_MAJOR}.x"
        )
    version: str = row[0]
    if int(version.split(".")[0]) != SUPPORTED_MAJOR:
        raise SchemaContractMismatch(
            f"contrato de schema {version} incompatível; esperado {SUPPORTED_MAJOR}.x"
        )
    return version


def open_run(conn: psycopg.Connection, source_id: int) -> int:
    """Abre uma execução (status RUNNING, contadores 0) e devolve o id."""
    try:
        with translate_db_errors():
            row = conn.execute(_OPEN_RUN_SQL, (source_id,)).fetchone()
            conn.commit()
    except Exception:
        conn.rollback()
        raise
    return row[0]


def close_run(
    conn: psycopg.Connection,
    run_id: int,
    status: RunStatus | str,
    counts: RunCounts,
    errors: list[RunError] | None = None,
) -> None:
    """Fecha a execução uma única vez, gravando status, contadores e a lista de erros."""
    try:
        final_status = RunStatus(status)
        counts = RunCounts.model_validate(counts)
        items = [RunError.model_validate(error).model_dump() for error in errors or []]
    except (ValueError, ValidationError):
        raise DbContractViolation(
            f"status deve ser um de {[s.value for s in RunStatus]} e contadores >= 0"
        ) from None
    try:
        with translate_db_errors():
            cursor = conn.execute(
                _CLOSE_RUN_SQL,
                (
                    final_status.value,
                    counts.created,
                    counts.updated,
                    counts.unchanged,
                    counts.failed,
                    Jsonb(items),
                    run_id,
                ),
            )
            if cursor.rowcount != 1:
                raise DbContractViolation(
                    f"ingestion_run {run_id} não existe ou já foi fechada (não está em RUNNING)"
                )
            conn.commit()
    except Exception:
        conn.rollback()
        raise
