from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from cultural_events_agent.errors import DbContractViolation
from cultural_events_agent.extraction import NormalizedEvent
from cultural_events_agent.mapping import (
    create_venue,
    is_generic_venue,
    load_categories,
    map_event,
    normalize_venue,
    resolve_category,
)
from cultural_events_agent.sources.base import Session

SP = ZoneInfo("America/Sao_Paulo")


def evento(**extra) -> NormalizedEvent:
    values = {
        "title": "Show",
        "sessions": [Session(starts_at=datetime(2026, 12, 1, 20, tzinfo=SP))],
        "venue_name": "Fundação Maria Luisa e Oscar Americano",
        "address_line": "Avenida Morumbi 4077",
        "postal_code": "05650-000",
        "latitude": -23.6,
        "longitude": -46.7,
    }
    values.update(extra)
    return NormalizedEvent(**values)


@pytest.fixture
def categories(writer_conn):
    return load_categories(writer_conn)


def test_categorias_do_banco(categories):
    assert {"shows", "teatro", "exposicoes", "danca", "cinema", "outros"} <= set(categories)


def test_slug_valido(categories):
    assert resolve_category("teatro", categories) == ("teatro", categories["teatro"])
    assert resolve_category(" Teatro ", categories)[0] == "teatro"


@pytest.mark.parametrize("slug", ["gastronomia", "", None, "sociedade-e-cultura"])
def test_slug_inventado_vira_outros(categories, slug):
    assert resolve_category(slug, categories) == ("outros", categories["outros"])


def test_sem_categoria_de_fallback_e_violacao_do_contrato(writer_conn, admin_conn):
    admin_conn.execute("UPDATE category SET slug = 'outros-x' WHERE slug = 'outros'")
    try:
        with pytest.raises(DbContractViolation):
            load_categories(writer_conn)
    finally:
        admin_conn.execute("UPDATE category SET slug = 'outros' WHERE slug = 'outros-x'")


def test_local_existente_com_grafia_diferente(writer_conn, city_id, categories):
    existente = create_venue(writer_conn, city_id, normalize_venue(evento()))
    mapped = map_event(
        writer_conn,
        evento(venue_name="Fundacao Maria Luisa e Oscar Americano"),
        city_id=city_id,
        categories=categories,
    )
    assert mapped.venue_id == existente
    assert mapped.venue_to_create is None


def test_local_novo_nao_e_criado_no_mapeamento(writer_conn, admin_conn, city_id, categories):
    mapped = map_event(writer_conn, evento(), city_id=city_id, categories=categories)
    assert mapped.venue_id is None
    assert mapped.venue_to_create.name == "Fundação Maria Luisa e Oscar Americano"
    assert admin_conn.execute("SELECT count(*) FROM venue").fetchone()[0] == 0


def test_criar_local_novo(writer_conn, admin_conn, city_id, categories):
    venue_id = create_venue(writer_conn, city_id, normalize_venue(evento()))
    row = admin_conn.execute(
        "SELECT city_id, name, address_line, postal_code FROM venue WHERE id = %s", (venue_id,)
    ).fetchone()
    assert row == (
        city_id,
        "Fundação Maria Luisa e Oscar Americano",
        "Avenida Morumbi 4077",
        "05650-000",
    )


def test_criar_local_repetido_devolve_o_existente(writer_conn, city_id):
    primeiro = create_venue(writer_conn, city_id, normalize_venue(evento()))
    assert create_venue(writer_conn, city_id, normalize_venue(evento())) == primeiro


@pytest.mark.parametrize("nome", ["Local a definir", "LOCAL A DEFINIR", "  ", None, "A definir"])
def test_local_a_definir_fica_sem_venue(writer_conn, admin_conn, city_id, categories, nome):
    mapped = map_event(writer_conn, evento(venue_name=nome), city_id=city_id, categories=categories)
    assert mapped.venue_id is None and mapped.venue_to_create is None
    assert admin_conn.execute("SELECT count(*) FROM venue").fetchone()[0] == 0


def test_venue_de_outra_cidade_nao_e_reutilizado(
    writer_conn, city_id, inactive_city_id, categories
):
    create_venue(writer_conn, inactive_city_id, normalize_venue(evento()))
    mapped = map_event(writer_conn, evento(), city_id=city_id, categories=categories)
    assert mapped.venue_id is None
    assert mapped.venue_to_create is not None


@pytest.mark.parametrize("cep", ["", "  ", "05650000", "5650-000", "abc", None])
def test_cep_invalido_vira_nulo(writer_conn, city_id, cep):
    venue = normalize_venue(evento(postal_code=cep))
    assert venue.postal_code is None
    assert create_venue(writer_conn, city_id, venue)  # o banco aceita


def test_coordenadas_fora_do_intervalo_viram_nulas(writer_conn, city_id):
    venue = normalize_venue(evento(latitude=123.0, longitude=-46.7))
    assert venue.latitude is None and venue.longitude is None
    assert create_venue(writer_conn, city_id, venue)


def test_nomes_genericos():
    assert is_generic_venue("Local a Definir")
    assert not is_generic_venue("Teatro Municipal")
