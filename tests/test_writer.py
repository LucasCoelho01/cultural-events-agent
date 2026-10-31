from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from cultural_events_agent import writer
from cultural_events_agent.db import open_run
from cultural_events_agent.errors import DbContractViolation, VersionConflict
from cultural_events_agent.extraction import NormalizedEvent
from cultural_events_agent.mapping import MappedEvent, VenueData, load_categories
from cultural_events_agent.sources.base import DiscoveredItem, Session
from cultural_events_agent.writer import Outcome, plan_item, read_link, write_item

SP = ZoneInfo("America/Sao_Paulo")
START = datetime(2026, 12, 12, 10, 30, tzinfo=SP)
VENUE = VenueData(
    "Fundação Oscar Americano", "Avenida Morumbi 4077", "Morumbi", "05650-000", None, None
)


def evento(**extra) -> NormalizedEvent:
    values = {
        "title": "Vamos falar de Agatha Christie",
        "description": "Uma conversa.",
        "sessions": [Session(starts_at=START, ends_at=START + timedelta(hours=3))],
        "price_min_cents": 10000,
        "price_max_cents": 11000,
        "ticket_url": "https://www.sympla.com.br/evento/x/1",
        "image_url": "https://images.example/x.png",
    }
    values.update(extra)
    return NormalizedEvent(**values)


@pytest.fixture
def categorias(writer_conn):
    return load_categories(writer_conn)


@pytest.fixture
def mapear(categorias):
    def build(event=None, **extra) -> MappedEvent:
        values = {
            "category_slug": "shows",
            "category_id": categorias["shows"],
            "venue_to_create": VENUE,
        }
        values.update(extra)
        return MappedEvent(event=event or evento(), **values)

    return build


@pytest.fixture
def runs(writer_conn, source_id):
    return lambda: open_run(writer_conn, source_id)


def item(external_id: str = "1") -> DiscoveredItem:
    return DiscoveredItem(external_id, f"https://www.sympla.com.br/evento/x/{external_id}", "x")


def gravar(conn, source_id, run_id, city_id, mapped, *, raw_hash="h1", external_id="1"):
    return write_item(
        conn,
        source_id=source_id,
        run_id=run_id,
        item=item(external_id),
        raw_hash=raw_hash,
        mapped=mapped,
        city_id=city_id,
    )


def linha_do_evento(admin_conn, title="Vamos falar de Agatha Christie"):
    return admin_conn.execute(
        "SELECT id, status, origin, version, price_min_cents, venue_id, title, category_id "
        "FROM event WHERE title = %s",
        (title,),
    ).fetchone()


def test_criar(writer_conn, admin_conn, source_id, city_id, runs, mapear):
    run_id = runs()
    assert gravar(writer_conn, source_id, run_id, city_id, mapear()) is Outcome.CREATED
    event_id, status, origin, version, price, venue_id, *_ = linha_do_evento(admin_conn)
    assert (status, origin, version, price) == ("PUBLISHED", "AGENT", 0, 10000)
    assert admin_conn.execute("SELECT name FROM venue WHERE id = %s", (venue_id,)).fetchone() == (
        "Fundação Oscar Americano",
    )
    assert admin_conn.execute(
        "SELECT starts_at, ends_at FROM occurrence WHERE event_id = %s", (event_id,)
    ).fetchall() == [(START, START + timedelta(hours=3))]
    assert admin_conn.execute(
        "SELECT event_id, content_hash, last_run_id, external_url FROM event_source_link"
    ).fetchone() == (event_id, "h1", run_id, "https://www.sympla.com.br/evento/x/1")


def test_repetir_sem_mudanca(writer_conn, admin_conn, source_id, city_id, runs, mapear):
    gravar(writer_conn, source_id, runs(), city_id, mapear())
    antes = admin_conn.execute("SELECT version, updated_at FROM event").fetchone()
    segunda = runs()
    assert gravar(writer_conn, source_id, segunda, city_id, mapear()) is Outcome.UNCHANGED
    assert admin_conn.execute("SELECT version, updated_at FROM event").fetchone() == antes
    assert admin_conn.execute("SELECT count(*) FROM event").fetchone()[0] == 1
    assert admin_conn.execute("SELECT last_run_id FROM event_source_link").fetchone() == (segunda,)


def test_mudanca_de_preco(writer_conn, admin_conn, source_id, city_id, runs, mapear):
    gravar(writer_conn, source_id, runs(), city_id, mapear())
    novo = mapear(evento(price_min_cents=11000, price_max_cents=12000))
    run_id = runs()
    assert gravar(writer_conn, source_id, run_id, city_id, novo, raw_hash="h2") is Outcome.UPDATED
    _, _, _, version, price, *_ = linha_do_evento(admin_conn)
    assert (version, price) == (1, 11000)
    assert admin_conn.execute(
        "SELECT content_hash, last_run_id FROM event_source_link"
    ).fetchone() == ("h2", run_id)
    assert admin_conn.execute("SELECT count(*) FROM occurrence").fetchone()[0] == 1


def test_campo_travado_e_preservado(writer_conn, admin_conn, source_id, city_id, runs, mapear):
    gravar(writer_conn, source_id, runs(), city_id, mapear())
    admin_conn.execute("UPDATE event SET title = 'Título corrigido', locked_fields = '{title}'")
    novo = mapear(
        evento(title="Título novo da fonte", price_min_cents=15000, price_max_cents=15000)
    )
    assert gravar(writer_conn, source_id, runs(), city_id, novo, raw_hash="h2") is Outcome.UPDATED
    title, price, version, locked = admin_conn.execute(
        "SELECT title, price_min_cents, version, locked_fields FROM event"
    ).fetchone()
    assert (title, price, version, locked) == ("Título corrigido", 15000, 1, ["title"])


def test_sessoes_travadas_nao_sao_reescritas(
    writer_conn, admin_conn, source_id, city_id, runs, mapear
):
    gravar(writer_conn, source_id, runs(), city_id, mapear())
    admin_conn.execute("UPDATE event SET locked_fields = '{occurrences}'")
    outra = Session(starts_at=START + timedelta(days=30))
    novo = mapear(evento(sessions=[outra], price_min_cents=9000, price_max_cents=9000))
    gravar(writer_conn, source_id, runs(), city_id, novo, raw_hash="h2")
    starts = admin_conn.execute("SELECT starts_at FROM occurrence").fetchall()
    assert starts == [(START,)]
    assert admin_conn.execute("SELECT price_min_cents FROM event").fetchone() == (9000,)


def test_lock_em_outra_coluna_nao_trava_as_sessoes(
    writer_conn, admin_conn, source_id, city_id, runs, mapear
):
    gravar(writer_conn, source_id, runs(), city_id, mapear())
    admin_conn.execute("UPDATE event SET locked_fields = '{title}'")
    outra = Session(starts_at=START + timedelta(days=30))
    gravar(writer_conn, source_id, runs(), city_id, mapear(evento(sessions=[outra])), raw_hash="h2")
    assert admin_conn.execute("SELECT starts_at FROM occurrence").fetchall() == [(outra.starts_at,)]


def test_agente_nunca_altera_locked_fields_nem_status(
    writer_conn, admin_conn, source_id, city_id, runs, mapear
):
    gravar(writer_conn, source_id, runs(), city_id, mapear())
    admin_conn.execute("UPDATE event SET locked_fields = '{price_min_cents}', status = 'CANCELLED'")
    gravar(writer_conn, source_id, runs(), city_id, mapear(evento(title="Outro")), raw_hash="h2")
    assert admin_conn.execute("SELECT status, locked_fields, title FROM event").fetchone() == (
        "CANCELLED",
        ["price_min_cents"],
        "Outro",
    )


def test_lapide_nao_recria(writer_conn, admin_conn, source_id, city_id, runs, mapear):
    gravar(writer_conn, source_id, runs(), city_id, mapear())
    admin_conn.execute("DELETE FROM event")
    link = read_link(writer_conn, source_id, "1")
    assert link.is_tombstone
    novo = mapear(evento(title="Mudou na fonte"))
    assert gravar(writer_conn, source_id, runs(), city_id, novo, raw_hash="h2") is Outcome.UNCHANGED
    assert admin_conn.execute("SELECT count(*) FROM event").fetchone()[0] == 0
    assert admin_conn.execute(
        "SELECT count(*), count(event_id) FROM event_source_link"
    ).fetchone() == (1, 0)


def test_rollback_do_item_inteiro(writer_conn, admin_conn, source_id, city_id, runs, mapear):
    invalida = Session.model_construct(starts_at=START, ends_at=START - timedelta(hours=1))
    quebrado = evento().model_copy(update={"sessions": [invalida]})
    with pytest.raises(DbContractViolation) as exc:
        gravar(writer_conn, source_id, runs(), city_id, mapear(quebrado))
    assert "23514" in exc.value.message
    for table in ("event", "occurrence", "venue", "event_source_link"):
        assert admin_conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0, table


def test_itens_independentes(writer_conn, admin_conn, source_id, city_id, runs, mapear):
    run_id = runs()
    invalida = Session.model_construct(starts_at=START, ends_at=START - timedelta(hours=1))
    itens = [
        ("1", mapear(evento(title="Primeiro evento"), venue_to_create=None)),
        ("2", mapear(evento(title="Segundo evento").model_copy(update={"sessions": [invalida]}))),
        ("3", mapear(evento(title="Terceiro evento"), venue_to_create=None)),
    ]
    falhas = []
    for external_id, mapped in itens:
        try:
            gravar(writer_conn, source_id, run_id, city_id, mapped, external_id=external_id)
        except DbContractViolation:
            falhas.append(external_id)
    assert falhas == ["2"]
    titulos = admin_conn.execute("SELECT title FROM event ORDER BY title").fetchall()
    assert titulos == [("Primeiro evento",), ("Terceiro evento",)]


@pytest.fixture
def outra_fonte(admin_conn, clean_db) -> int:
    return admin_conn.execute(
        "INSERT INTO source (code, name, base_url) VALUES ('outra', 'Outra', 'https://outra.test') "
        "RETURNING id"
    ).fetchone()[0]


def test_dedupe_entre_fontes_so_cria_o_vinculo(
    writer_conn, admin_conn, source_id, outra_fonte, city_id, runs, mapear
):
    run_id = runs()
    gravar(writer_conn, outra_fonte, run_id, city_id, mapear(), external_id="a1")
    parecido = mapear(evento(title="Vamos falar de Agatha Christie!", price_min_cents=5))
    result = gravar(writer_conn, source_id, run_id, city_id, parecido, external_id="1")
    assert result is Outcome.UNCHANGED
    assert admin_conn.execute("SELECT count(*) FROM event").fetchone()[0] == 1
    assert admin_conn.execute("SELECT price_min_cents FROM event").fetchone() == (10000,)
    ligacoes = admin_conn.execute(
        "SELECT source_id FROM event_source_link ORDER BY source_id"
    ).fetchall()
    assert sorted(ligacoes) == sorted([(source_id,), (outra_fonte,)])
    assert admin_conn.execute(
        "SELECT count(DISTINCT event_id) FROM event_source_link"
    ).fetchone() == (1,)


def test_dedupe_sem_correspondencia_cria(
    writer_conn, admin_conn, source_id, outra_fonte, city_id, runs, mapear
):
    run_id = runs()
    gravar(writer_conn, outra_fonte, run_id, city_id, mapear(), external_id="a1")
    # título parecido, mas a sessão está fora da janela de 1 dia: é outro evento
    outro_dia = Session(starts_at=START + timedelta(days=5))
    outro = mapear(evento(title="Vamos falar de Agatha Christie 2", sessions=[outro_dia]))
    result = gravar(writer_conn, source_id, run_id, city_id, outro, external_id="1")
    assert result is Outcome.CREATED
    assert admin_conn.execute("SELECT count(*) FROM event").fetchone()[0] == 2


def test_dedupe_ignora_evento_da_mesma_fonte(
    writer_conn, admin_conn, source_id, city_id, runs, mapear
):
    run_id = runs()
    gravar(writer_conn, source_id, run_id, city_id, mapear(), external_id="1")
    parecido = mapear(evento(title="Vamos falar de Agatha Christie 2"))
    # título parecido e mesmo dia, mas da MESMA fonte: item distinto, não duplicata
    assert (
        gravar(writer_conn, source_id, run_id, city_id, parecido, external_id="2")
        is Outcome.CREATED
    )
    assert admin_conn.execute("SELECT count(*) FROM event").fetchone()[0] == 2


def test_slug_duplicado_falha_so_o_item(writer_conn, admin_conn, source_id, city_id, runs, mapear):
    run_id = runs()
    gravar(writer_conn, source_id, run_id, city_id, mapear(venue_to_create=None), external_id="1")
    mais_tarde = Session(starts_at=START + timedelta(days=10))
    repetido = mapear(evento(sessions=[mais_tarde]), venue_to_create=None)
    with pytest.raises(DbContractViolation) as exc:
        gravar(writer_conn, source_id, run_id, city_id, repetido, external_id="2")
    assert "23505" in exc.value.message
    assert admin_conn.execute("SELECT count(*) FROM event").fetchone()[0] == 1


def _desatualizar(monkeypatch, vezes: int):
    original = writer._event_state
    chamadas = {"n": 0}

    def estado(conn, event_id):
        version, locked = original(conn, event_id)
        chamadas["n"] += 1
        return (version - 1, locked) if chamadas["n"] <= vezes else (version, locked)

    monkeypatch.setattr(writer, "_event_state", estado)
    return chamadas


def test_conflito_de_versao_relê_e_conclui(
    writer_conn, admin_conn, source_id, city_id, runs, mapear, monkeypatch
):
    gravar(writer_conn, source_id, runs(), city_id, mapear())
    chamadas = _desatualizar(monkeypatch, vezes=1)
    novo = mapear(evento(price_min_cents=12000, price_max_cents=12000))
    assert gravar(writer_conn, source_id, runs(), city_id, novo, raw_hash="h2") is Outcome.UPDATED
    assert chamadas["n"] == 2
    assert admin_conn.execute("SELECT price_min_cents, version FROM event").fetchone() == (12000, 1)


def test_conflito_de_versao_persistente_vira_version_conflict(
    writer_conn, admin_conn, source_id, city_id, runs, mapear, monkeypatch
):
    gravar(writer_conn, source_id, runs(), city_id, mapear())
    chamadas = _desatualizar(monkeypatch, vezes=99)
    novo = mapear(evento(price_min_cents=12000, price_max_cents=12000))
    with pytest.raises(VersionConflict):
        gravar(writer_conn, source_id, runs(), city_id, novo, raw_hash="h2")
    assert chamadas["n"] == 3
    assert admin_conn.execute("SELECT price_min_cents, version FROM event").fetchone() == (10000, 0)
    assert admin_conn.execute("SELECT content_hash FROM event_source_link").fetchone() == ("h1",)


def test_atualizacao_cria_venue_novo_na_transacao(
    writer_conn, admin_conn, source_id, city_id, runs, mapear
):
    gravar(writer_conn, source_id, runs(), city_id, mapear(venue_to_create=None))
    assert admin_conn.execute("SELECT venue_id FROM event").fetchone() == (None,)
    gravar(writer_conn, source_id, runs(), city_id, mapear(), raw_hash="h2")
    venue_id = admin_conn.execute("SELECT venue_id FROM event").fetchone()[0]
    assert venue_id is not None
    assert admin_conn.execute("SELECT count(*) FROM venue").fetchone()[0] == 1


def test_plano_do_dry_run_nao_grava(writer_conn, admin_conn, source_id, city_id, runs, mapear):
    args = {"source_id": source_id, "item": item(), "mapped": mapear(), "city_id": city_id}
    assert plan_item(writer_conn, raw_hash="h1", **args) == "create"
    assert admin_conn.execute("SELECT count(*) FROM event_source_link").fetchone()[0] == 0
    gravar(writer_conn, source_id, runs(), city_id, mapear())
    assert plan_item(writer_conn, raw_hash="h1", **args) == "unchanged"
    assert plan_item(writer_conn, raw_hash="h2", **args) == "update"
    admin_conn.execute("DELETE FROM event")
    assert plan_item(writer_conn, raw_hash="h2", **args) == "unchanged"
