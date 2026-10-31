"""Gravação de eventos conforme database/AGENT_CONTRACT.md (contrato 1.0), seções 4 a 8.

Todo SQL usa placeholders; os únicos nomes de coluna montados em código vêm de `UPDATABLE_COLUMNS`,
uma lista fixa. Cada item é uma transação própria: falhou, o item inteiro volta.
"""

from dataclasses import dataclass
from enum import StrEnum

import psycopg
from psycopg import sql

from cultural_events_agent.db import translate_db_errors
from cultural_events_agent.errors import VersionConflict
from cultural_events_agent.mapping import MappedEvent, create_venue
from cultural_events_agent.sources.base import DiscoveredItem

TITLE_SIMILARITY = 0.6  # limiar sugerido pelo contrato (seção 8); calibrar com dados reais
MAX_UPDATE_ATTEMPTS = 3
OCCURRENCES_LOCK = "occurrences"  # nome especial em locked_fields (não é coluna)

# Colunas que o agente atualiza. `status` e `locked_fields` nunca entram.
UPDATABLE_COLUMNS = (
    "title",
    "description",
    "category_id",
    "venue_id",
    "age_rating",
    "price_min_cents",
    "price_max_cents",
    "is_free",
    "ticket_url",
    "image_url",
)

# Seção 4: procura o vínculo travando a linha (FOR UPDATE).
_LINK_LOCK_SQL = """
    SELECT id, event_id, content_hash FROM event_source_link
     WHERE source_id = %s AND external_id = %s
     FOR UPDATE
"""
_LINK_READ_SQL = """
    SELECT id, event_id, content_hash FROM event_source_link
     WHERE source_id = %s AND external_id = %s
"""
_TOUCH_LINK_SQL = """
    UPDATE event_source_link SET last_seen_at = now(), last_run_id = %s WHERE id = %s
"""
# Seção 5: criação.
_INSERT_EVENT_SQL = """
    INSERT INTO event (city_id, category_id, venue_id, title, slug, description, status, origin,
                       age_rating, price_min_cents, price_max_cents, is_free, ticket_url, image_url)
    VALUES (%s, %s, %s, %s, NULL, %s, 'PUBLISHED', 'AGENT', %s, %s, %s, %s, %s, %s)
    RETURNING id
"""
_INSERT_OCCURRENCE_SQL = "INSERT INTO occurrence (event_id, starts_at, ends_at) VALUES (%s, %s, %s)"
_INSERT_LINK_SQL = """
    INSERT INTO event_source_link (event_id, source_id, external_id, external_url, content_hash,
                                   last_run_id)
    VALUES (%s, %s, %s, %s, %s, %s)
"""
# Seção 7: atualização com versão e locks.
_EVENT_STATE_SQL = "SELECT version, locked_fields FROM event WHERE id = %s FOR UPDATE"
_UPSERT_OCCURRENCE_SQL = """
    INSERT INTO occurrence (event_id, starts_at, ends_at, status) VALUES (%s, %s, %s, 'SCHEDULED')
    ON CONFLICT (event_id, starts_at) DO UPDATE
       SET ends_at = EXCLUDED.ends_at, status = EXCLUDED.status
"""
_DELETE_OCCURRENCES_SQL = "DELETE FROM occurrence WHERE event_id = %s AND starts_at <> ALL(%s)"
_FINISH_LINK_SQL = """
    UPDATE event_source_link
       SET content_hash = %s, external_url = %s, last_seen_at = now(), last_run_id = %s
     WHERE source_id = %s AND external_id = %s
"""
# Seção 8: o mesmo evento vindo de outra fonte (não do mesmo vínculo desta fonte).
_DUPLICATE_SQL = """
    SELECT e.id FROM event e
     WHERE e.city_id = %s
       AND similarity(e.title, %s) >= %s
       AND EXISTS (SELECT 1 FROM occurrence o
                    WHERE o.event_id = e.id
                      AND o.starts_at BETWEEN %s - interval '1 day' AND %s + interval '1 day')
       AND NOT EXISTS (SELECT 1 FROM event_source_link l
                        WHERE l.event_id = e.id AND l.source_id = %s)
     ORDER BY similarity(e.title, %s) DESC, e.id
     LIMIT 1
"""
# Seção 8: só o dono atualiza o evento: origin = 'AGENT' e o vínculo mais antigo é desta fonte.
_OWNER_SQL = """
    SELECT e.origin = 'AGENT'
           AND (SELECT l.source_id FROM event_source_link l
                 WHERE l.event_id = e.id ORDER BY l.id LIMIT 1) = %s
      FROM event e WHERE e.id = %s
"""


class Outcome(StrEnum):
    CREATED = "created"
    UPDATED = "updated"
    UNCHANGED = "unchanged"


@dataclass(frozen=True)
class Link:
    id: int
    event_id: int | None
    content_hash: str | None

    @property
    def is_tombstone(self) -> bool:
        """Lápide: o evento foi excluído à mão e o par (fonte, external_id) não volta."""
        return self.event_id is None


def read_link(conn: psycopg.Connection, source_id: int, external_id: str) -> Link | None:
    """Lê o vínculo sem travar (triagem, lápide antes do download e --dry-run)."""
    row = conn.execute(_LINK_READ_SQL, (source_id, external_id)).fetchone()
    return Link(*row) if row else None


def touch_link(conn: psycopg.Connection, link_id: int, run_id: int) -> None:
    """Hash igual: só registra que o item foi visto nesta execução."""
    with translate_db_errors(), conn.transaction():
        conn.execute(_TOUCH_LINK_SQL, (run_id, link_id))


def find_duplicate(
    conn: psycopg.Connection, source_id: int, city_id: int, title: str, first_start
) -> int | None:
    row = conn.execute(
        _DUPLICATE_SQL,
        (city_id, title, TITLE_SIMILARITY, first_start, first_start, source_id, title),
    ).fetchone()
    return row[0] if row else None


def owns_event(conn: psycopg.Connection, source_id: int, event_id: int) -> bool:
    """A primeira fonte é a dona (contrato §8): evento do agente cujo vínculo mais antigo é este."""
    row = conn.execute(_OWNER_SQL, (source_id, event_id)).fetchone()
    return bool(row and row[0])


def plan_item(
    conn: psycopg.Connection,
    *,
    source_id: int,
    item: DiscoveredItem,
    raw_hash: str,
    mapped: MappedEvent,
    city_id: int,
) -> str:
    """Ação que `write_item` tomaria, sem gravar nada (usada pelo --dry-run)."""
    link = read_link(conn, source_id, item.external_id)
    if link is None:
        first = mapped.event.sessions[0].starts_at
        duplicate = find_duplicate(conn, source_id, city_id, mapped.event.title, first)
        return "link_existing" if duplicate else "create"
    if link.is_tombstone or link.content_hash == raw_hash:
        return "unchanged"
    if not owns_event(conn, source_id, link.event_id):
        return "unchanged"
    return "update"


def write_item(
    conn: psycopg.Connection,
    *,
    source_id: int,
    run_id: int,
    item: DiscoveredItem,
    raw_hash: str,
    mapped: MappedEvent,
    city_id: int,
) -> Outcome:
    """Grava um item numa transação própria; qualquer falha desfaz o item inteiro."""
    with translate_db_errors(), conn.transaction():
        link = conn.execute(_LINK_LOCK_SQL, (source_id, item.external_id)).fetchone()
        if link is None:
            return _create(conn, source_id, run_id, item, raw_hash, mapped, city_id)
        current = Link(*link)
        if current.is_tombstone:
            return Outcome.UNCHANGED
        if current.content_hash == raw_hash:
            conn.execute(_TOUCH_LINK_SQL, (run_id, current.id))
            return Outcome.UNCHANGED
        if not owns_event(conn, source_id, current.event_id):
            # Vínculo por deduplicação: o evento é de outra fonte ou do admin; só o vínculo anda.
            conn.execute(
                _FINISH_LINK_SQL, (raw_hash, item.url, run_id, source_id, item.external_id)
            )
            return Outcome.UNCHANGED
        _update(conn, source_id, run_id, item, raw_hash, mapped, current.event_id, city_id)
        return Outcome.UPDATED


def _venue_id(conn: psycopg.Connection, mapped: MappedEvent, city_id: int) -> int | None:
    if mapped.venue_to_create is not None:
        return create_venue(conn, city_id, mapped.venue_to_create)
    return mapped.venue_id


def _create(
    conn: psycopg.Connection,
    source_id: int,
    run_id: int,
    item: DiscoveredItem,
    raw_hash: str,
    mapped: MappedEvent,
    city_id: int,
) -> Outcome:
    event = mapped.event
    duplicate = find_duplicate(conn, source_id, city_id, event.title, event.sessions[0].starts_at)
    if duplicate is not None:
        # A primeira fonte é a dona: só o vínculo da nova fonte, sem tocar no evento.
        conn.execute(
            _INSERT_LINK_SQL, (duplicate, source_id, item.external_id, item.url, raw_hash, run_id)
        )
        return Outcome.UNCHANGED
    venue_id = _venue_id(conn, mapped, city_id)
    event_id = conn.execute(
        _INSERT_EVENT_SQL,
        (
            city_id,
            mapped.category_id,
            venue_id,
            event.title,
            event.description,
            event.age_rating,
            event.price_min_cents,
            event.price_max_cents,
            event.is_free,
            event.ticket_url,
            event.image_url,
        ),
    ).fetchone()[0]
    with conn.cursor() as cursor:
        cursor.executemany(
            _INSERT_OCCURRENCE_SQL, [(event_id, s.starts_at, s.ends_at) for s in event.sessions]
        )
    conn.execute(
        _INSERT_LINK_SQL, (event_id, source_id, item.external_id, item.url, raw_hash, run_id)
    )
    return Outcome.CREATED


def _event_state(conn: psycopg.Connection, event_id: int) -> tuple[int, list[str]]:
    version, locked = conn.execute(_EVENT_STATE_SQL, (event_id,)).fetchone()
    return version, list(locked or [])


def _update(
    conn: psycopg.Connection,
    source_id: int,
    run_id: int,
    item: DiscoveredItem,
    raw_hash: str,
    mapped: MappedEvent,
    event_id: int,
    city_id: int,
) -> None:
    event = mapped.event
    for _ in range(MAX_UPDATE_ATTEMPTS):
        version, locked = _event_state(conn, event_id)
        values = {
            "title": event.title,
            "description": event.description,
            "category_id": mapped.category_id,
            "age_rating": event.age_rating,
            "price_min_cents": event.price_min_cents,
            "price_max_cents": event.price_max_cents,
            "is_free": event.is_free,
            "ticket_url": event.ticket_url,
            "image_url": event.image_url,
        }
        if "venue_id" not in locked:
            values["venue_id"] = _venue_id(conn, mapped, city_id)
        columns = [c for c in UPDATABLE_COLUMNS if c in values and c not in locked]
        if columns and not _update_columns(conn, event_id, version, columns, values):
            continue  # a versão mudou no meio: relê e reavalia
        if OCCURRENCES_LOCK not in locked:
            _rewrite_occurrences(conn, event_id, event)
        conn.execute(_FINISH_LINK_SQL, (raw_hash, item.url, run_id, source_id, item.external_id))
        return
    raise VersionConflict(
        f"o evento {event_id} mudou durante {MAX_UPDATE_ATTEMPTS} tentativas de atualização"
    )


def _update_columns(
    conn: psycopg.Connection, event_id: int, version: int, columns: list[str], values: dict
) -> bool:
    assignments = sql.SQL(", ").join(
        sql.SQL("{} = %s").format(sql.Identifier(column)) for column in columns
    )
    statement = sql.SQL(
        "UPDATE event SET {assignments}, version = version + 1 WHERE id = %s AND version = %s"
    ).format(assignments=assignments)
    cursor = conn.execute(statement, [*(values[c] for c in columns), event_id, version])
    return cursor.rowcount == 1


def _rewrite_occurrences(conn: psycopg.Connection, event_id: int, event) -> None:
    with conn.cursor() as cursor:
        cursor.executemany(
            _UPSERT_OCCURRENCE_SQL, [(event_id, s.starts_at, s.ends_at) for s in event.sessions]
        )
    conn.execute(_DELETE_OCCURRENCES_SQL, (event_id, [s.starts_at for s in event.sessions]))
