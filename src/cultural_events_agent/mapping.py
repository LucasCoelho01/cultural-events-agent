"""Mapeamento para o banco: categoria por slug (fallback `outros`) e local por similaridade.

A leitura aqui não grava nada. O `venue` novo só é criado dentro da transação do evento
(`create_venue`, chamada pelo writer), para o rollback do item levar o local junto.
"""

import re
import unicodedata
from dataclasses import dataclass

import psycopg

from cultural_events_agent.errors import DbContractViolation
from cultural_events_agent.extraction import NormalizedEvent

FALLBACK_CATEGORY = "outros"
VENUE_SIMILARITY = 0.6  # limiar sugerido pelo contrato; calibrar com dados reais

_POSTAL_CODE = re.compile(r"^\d{5}-\d{3}$")
_GENERIC_VENUES = {"", "local a definir", "a definir", "local indefinido", "local nao informado"}

_CATEGORIES_SQL = "SELECT slug, id FROM category"
_FIND_VENUE_SQL = """
    SELECT id FROM venue
     WHERE city_id = %s AND similarity(name, %s) >= %s
     ORDER BY similarity(name, %s) DESC, id
     LIMIT 1
"""
# Seção 5 do contrato: o slug sai do nome; em conflito, devolve o venue que já existe.
_CREATE_VENUE_SQL = """
    INSERT INTO venue (city_id, name, slug, address_line, neighborhood, postal_code,
                       latitude, longitude)
    VALUES (%s, %s, NULL, %s, %s, %s, %s, %s)
    ON CONFLICT (city_id, slug) DO UPDATE SET name = venue.name
    RETURNING id
"""


@dataclass(frozen=True)
class VenueData:
    name: str
    address_line: str | None
    neighborhood: str | None
    postal_code: str | None
    latitude: float | None
    longitude: float | None


@dataclass(frozen=True)
class MappedEvent:
    event: NormalizedEvent
    category_slug: str
    category_id: int
    venue_id: int | None = None  # local que já existe
    venue_to_create: VenueData | None = None  # local novo, criado na transação do evento


def load_categories(conn: psycopg.Connection) -> dict[str, int]:
    """Slug -> id das categorias; o agente nunca cria categorias."""
    categories = {slug: cid for slug, cid in conn.execute(_CATEGORIES_SQL).fetchall()}
    if FALLBACK_CATEGORY not in categories:
        raise DbContractViolation(f"a categoria de fallback '{FALLBACK_CATEGORY}' não existe")
    return categories


def resolve_category(slug: str | None, categories: dict[str, int]) -> tuple[str, int]:
    """Slug escolhido se existir; qualquer outro valor vira `outros`."""
    chosen = (slug or "").strip().lower()
    if chosen not in categories:
        chosen = FALLBACK_CATEGORY
    return chosen, categories[chosen]


def is_generic_venue(name: str | None) -> bool:
    folded = unicodedata.normalize("NFKD", (name or "").lower())
    plain = "".join(ch for ch in folded if not unicodedata.combining(ch))
    return " ".join(plain.split()) in _GENERIC_VENUES


def normalize_venue(event: NormalizedEvent) -> VenueData:
    """Dados do venue já compatíveis com as constraints do banco (valor inválido vira nulo)."""
    postal = (event.postal_code or "").strip()
    lat, lon = event.latitude, event.longitude
    valid_geo = lat is not None and lon is not None and -90 <= lat <= 90 and -180 <= lon <= 180
    return VenueData(
        name=(event.venue_name or "").strip(),
        address_line=(event.address_line or "").strip() or None,
        neighborhood=(event.neighborhood or "").strip() or None,
        postal_code=postal if _POSTAL_CODE.match(postal) else None,
        latitude=lat if valid_geo else None,
        longitude=lon if valid_geo else None,
    )


def find_venue(conn: psycopg.Connection, city_id: int, name: str) -> int | None:
    """O venue mais parecido na cidade do evento, se atingir o limiar."""
    row = conn.execute(_FIND_VENUE_SQL, (city_id, name, VENUE_SIMILARITY, name)).fetchone()
    return row[0] if row else None


def create_venue(conn: psycopg.Connection, city_id: int, venue: VenueData) -> int:
    row = conn.execute(
        _CREATE_VENUE_SQL,
        (
            city_id,
            venue.name,
            venue.address_line,
            venue.neighborhood,
            venue.postal_code,
            venue.latitude,
            venue.longitude,
        ),
    ).fetchone()
    return row[0]


def map_event(
    conn: psycopg.Connection,
    event: NormalizedEvent,
    *,
    city_id: int,
    categories: dict[str, int],
) -> MappedEvent:
    """Resolve categoria e local sem gravar; o local novo fica para a transação do evento."""
    slug, category_id = resolve_category(event.category_slug, categories)
    if is_generic_venue(event.venue_name):
        return MappedEvent(event, slug, category_id)
    venue = normalize_venue(event)
    venue_id = find_venue(conn, city_id, venue.name)
    if venue_id is not None:
        return MappedEvent(event, slug, category_id, venue_id=venue_id)
    return MappedEvent(event, slug, category_id, venue_to_create=venue)
