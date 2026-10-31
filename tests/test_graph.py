import io
import json
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx
import psycopg
import pytest

from cultural_events_agent import graph
from cultural_events_agent.errors import (
    CityInactive,
    CityNotFound,
    DbUnavailable,
    FetchFailed,
    LlmUnavailable,
    RobotsDisallowed,
    SourceNotFound,
)
from cultural_events_agent.fetching import PoliteFetcher
from cultural_events_agent.graph import run_ingestion
from cultural_events_agent.llm import FakeLlm
from cultural_events_agent.sources.base import DiscoveredItem, Session, StructuredEvent
from cultural_events_agent.sources.sympla import SymplaSource

SP = ZoneInfo("America/Sao_Paulo")
NOW = datetime(2026, 10, 30, 12, 0, tzinfo=SP)
SYMPLA = SymplaSource()
LISTAGEM_URL = "https://www.sympla.com.br/eventos/sao-paulo-sp"
AGATHA = ("vamos-falar-de-agatha-christie-luiz-alfredo-garcia-roza", "3298539")
GRATUITO = ("sarau-gratuito-no-parque", "3500001")
GOUVEA = ("gouvea-pass", "3346586")
TESTE_SLUG = ("evento-teste-565", "3430776")
LLM_AGATHA = {"category_slug": "teatro", "price_min_cents": 10000, "price_max_cents": 11000}
LLM_GRATUITO = {"category_slug": "outros", "is_free": True}


def url_do_evento(par: tuple[str, str]) -> str:
    return f"https://www.sympla.com.br/evento/{par[0]}/{par[1]}"


def listagem(*pares: tuple[str, str]) -> str:
    links = "".join(f'<a href="/evento/{slug}/{ident}?mp=1">e</a>' for slug, ident in pares)
    return f"<html><body>{links}</body></html>"


class FakeFetcher:
    """Serve páginas em memória e registra o que foi pedido (sem rede)."""

    def __init__(self, listings: dict[str, str], events: dict[str, str | Exception], max_pages=50):
        self.listings, self.events, self.max_pages = listings, events, max_pages
        self.listing_calls: list[str] = []
        self.event_calls: list[str] = []

    @property
    def pages_remaining(self) -> int:
        return max(self.max_pages - len(self.listing_calls) - len(self.event_calls), 0)

    def get_listing(self, url: str) -> str:
        self.listing_calls.append(url)
        if url not in self.listings:
            raise FetchFailed(f"sem listagem em {url}")
        return self.listings[url]

    def close(self) -> None:
        pass

    def get_event(self, url: str) -> str:
        self.event_calls.append(url)
        page = self.events[url]
        if isinstance(page, Exception):
            raise page
        return page


def sympla_fetcher(sympla_html, *itens: tuple[tuple[str, str], str], **kw) -> FakeFetcher:
    pares = [par for par, _ in itens]
    events = {url_do_evento(par): sympla_html(arquivo) for par, arquivo in itens}
    return FakeFetcher({LISTAGEM_URL: listagem(*pares)}, events, **kw)


def executar(conn, make_settings, llm, fetcher, source=SYMPLA, **kw):
    out = io.StringIO()
    result = run_ingestion(
        conn,
        make_settings(request_delay_seconds=0),
        source,
        llm,
        fetcher,
        state_uf=kw.pop("state_uf", "SP"),
        city_slug=kw.pop("city_slug", "sao-paulo"),
        now=lambda: NOW,
        out=out,
        **kw,
    )
    result.stdout = out.getvalue()
    return result


def contar(admin_conn, *tabelas: str) -> list[int]:
    return [admin_conn.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in tabelas]


def test_criar_e_rodar_de_novo_chama_o_llm_so_na_primeira(
    writer_conn, admin_conn, make_settings, sympla_html, city_id
):
    llm = FakeLlm(structured=LLM_AGATHA)
    fetcher = sympla_fetcher(sympla_html, (AGATHA, "evento_agatha.html"))

    primeira = executar(writer_conn, make_settings, llm, fetcher)
    assert (primeira.counts.created, primeira.counts.unchanged) == (1, 0)
    assert primeira.status == "SUCCESS"
    assert len(llm.prompts) == 1
    assert contar(admin_conn, "event", "occurrence", "venue", "event_source_link") == [1, 1, 1, 1]

    segunda = executar(writer_conn, make_settings, llm, fetcher)
    assert (segunda.counts.created, segunda.counts.unchanged) == (0, 1)
    assert len(llm.prompts) == 1  # o modelo não foi chamado de novo
    runs = admin_conn.execute(
        "SELECT status, created, updated, unchanged, failed FROM ingestion_run ORDER BY id"
    ).fetchall()
    assert runs == [("SUCCESS", 1, 0, 0, 0), ("SUCCESS", 0, 0, 1, 0)]
    assert admin_conn.execute("SELECT last_run_id FROM event_source_link").fetchone() == (
        segunda.run_id,
    )


def test_mudanca_de_preco_atualiza_e_chama_o_modelo_de_novo(
    writer_conn, admin_conn, make_settings, sympla_html, city_id
):
    llm = FakeLlm(
        structured=[LLM_AGATHA, {**LLM_AGATHA, "price_min_cents": 11000, "price_max_cents": 12000}]
    )
    executar(
        writer_conn, make_settings, llm, sympla_fetcher(sympla_html, (AGATHA, "evento_agatha.html"))
    )
    nova = sympla_fetcher(sympla_html, (AGATHA, "evento_agatha_preco110.html"))
    resultado = executar(writer_conn, make_settings, llm, nova)
    assert (resultado.counts.updated, resultado.counts.created) == (1, 0)
    assert len(llm.prompts) == 2
    assert admin_conn.execute("SELECT price_min_cents, version FROM event").fetchone() == (11000, 1)


def test_campo_volatil_nao_chama_o_modelo(writer_conn, make_settings, sympla_html, city_id):
    llm = FakeLlm(structured=LLM_AGATHA)
    executar(
        writer_conn, make_settings, llm, sympla_fetcher(sympla_html, (AGATHA, "evento_agatha.html"))
    )
    volatil = sympla_fetcher(sympla_html, (AGATHA, "evento_agatha_volatil.html"))
    assert executar(writer_conn, make_settings, llm, volatil).counts.unchanged == 1
    assert len(llm.prompts) == 1


def test_campo_travado_e_preservado_no_fluxo(
    writer_conn, admin_conn, make_settings, sympla_html, city_id
):
    llm = FakeLlm(
        structured=[LLM_AGATHA, {**LLM_AGATHA, "price_min_cents": 11000, "price_max_cents": 11000}]
    )
    executar(
        writer_conn, make_settings, llm, sympla_fetcher(sympla_html, (AGATHA, "evento_agatha.html"))
    )
    admin_conn.execute("UPDATE event SET title = 'Título corrigido', locked_fields = '{title}'")
    nova = sympla_fetcher(sympla_html, (AGATHA, "evento_agatha_preco110.html"))
    assert executar(writer_conn, make_settings, llm, nova).counts.updated == 1
    assert admin_conn.execute("SELECT title, price_min_cents FROM event").fetchone() == (
        "Título corrigido",
        11000,
    )


def test_lapide_nao_recria_e_nao_baixa_a_pagina(
    writer_conn, admin_conn, make_settings, sympla_html, city_id
):
    llm = FakeLlm(structured=LLM_AGATHA)
    fetcher = sympla_fetcher(sympla_html, (AGATHA, "evento_agatha.html"))
    executar(writer_conn, make_settings, llm, fetcher)
    admin_conn.execute("DELETE FROM event")
    resultado = executar(writer_conn, make_settings, llm, fetcher)
    assert resultado.counts.unchanged == 1 and resultado.counts.created == 0
    assert fetcher.event_calls == [url_do_evento(AGATHA)]  # só na primeira execução
    assert contar(admin_conn, "event") == [0]
    assert admin_conn.execute(
        "SELECT count(*), count(event_id) FROM event_source_link"
    ).fetchone() == (1, 0)


def _polite(make_settings, robots: str, listing_html: str, events: dict[str, str]):
    class Renderer:
        def render(self, url, timeout_seconds):
            return events[url]

        def close(self):
            pass

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text=robots)
        if request.url.path == "/eventos/sao-paulo-sp":
            return httpx.Response(200, text=listing_html)
        return httpx.Response(500, text="erro")

    return PoliteFetcher(
        make_settings(request_delay_seconds=0),
        http=httpx.Client(transport=httpx.MockTransport(handler)),
        renderer=Renderer(),
        sleep=lambda s: None,
    )


def test_robots_bloqueia_um_evento_e_a_execucao_segue(
    writer_conn, admin_conn, make_settings, sympla_html, city_id
):
    events = {
        url_do_evento(AGATHA): sympla_html("evento_agatha.html"),
        url_do_evento(GRATUITO): sympla_html("evento_gratuito.html"),
    }
    fetcher = _polite(
        make_settings,
        f"User-agent: *\nDisallow: /evento/{GRATUITO[0]}/\n",
        listagem(AGATHA, GRATUITO),
        events,
    )
    llm = FakeLlm(structured=LLM_AGATHA)
    resultado = executar(writer_conn, make_settings, llm, fetcher)
    assert resultado.status == "PARTIAL"
    assert (resultado.counts.created, resultado.counts.failed) == (1, 1)
    assert "ROBOTS_DISALLOWED" in resultado.errors[0].message
    assert resultado.errors[0].external_id == GRATUITO[1]
    row = admin_conn.execute("SELECT status, failed, errors FROM ingestion_run").fetchone()
    assert row[0] == "PARTIAL" and row[1] == 1
    assert row[2][0]["external_id"] == GRATUITO[1]


def test_robots_bloqueia_a_listagem_e_fecha_como_failed(
    writer_conn, admin_conn, make_settings, sympla_html, city_id
):
    fetcher = _polite(make_settings, "User-agent: *\nDisallow: /eventos\n", listagem(AGATHA), {})
    with pytest.raises(RobotsDisallowed):
        executar(writer_conn, make_settings, FakeLlm(), fetcher)
    row = admin_conn.execute("SELECT status, errors FROM ingestion_run").fetchone()
    assert row[0] == "FAILED" and "ROBOTS_DISALLOWED" in row[1][0]["message"]
    assert contar(admin_conn, "event") == [0]


def test_falha_na_listagem_fecha_como_failed(writer_conn, admin_conn, make_settings, city_id):
    fetcher = FakeFetcher({}, {})
    with pytest.raises(FetchFailed):
        executar(writer_conn, make_settings, FakeLlm(), fetcher)
    assert admin_conn.execute("SELECT status FROM ingestion_run").fetchone() == ("FAILED",)


def test_extracao_invalida_so_falha_o_item(
    writer_conn, admin_conn, make_settings, sympla_html, city_id
):
    llm = FakeLlm(
        structured=[
            {"category_slug": "teatro", "price_min_cents": 2000, "price_max_cents": 1000},
            LLM_GRATUITO,
        ]
    )
    fetcher = sympla_fetcher(
        sympla_html, (AGATHA, "evento_agatha.html"), (GRATUITO, "evento_gratuito.html")
    )
    resultado = executar(writer_conn, make_settings, llm, fetcher)
    assert resultado.status == "PARTIAL"
    assert (resultado.counts.created, resultado.counts.failed) == (1, 1)
    assert resultado.errors[0].external_id == AGATHA[1]
    assert "EXTRACTION_INVALID" in resultado.errors[0].message
    assert admin_conn.execute("SELECT title FROM event").fetchall() == [
        ("SARAU GRATUITO NO PARQUE",)
    ]


def test_eventos_de_teste_sao_skipped(
    writer_conn, admin_conn, make_settings, sympla_html, city_id, caplog
):
    llm = FakeLlm(structured=LLM_AGATHA)
    fetcher = FakeFetcher(
        {LISTAGEM_URL: listagem(TESTE_SLUG, AGATHA, ("show-de-som", "3500004"))},
        {
            url_do_evento(AGATHA): sympla_html("evento_agatha.html"),
            url_do_evento(("show-de-som", "3500004")): sympla_html("evento_teste_titulo.html"),
        },
    )
    with caplog.at_level("INFO", logger="cultural_events_agent"):
        resultado = executar(writer_conn, make_settings, llm, fetcher)
    assert url_do_evento(TESTE_SLUG) not in fetcher.event_calls  # slug: sem download
    assert len(llm.prompts) == 1  # título: skipped antes do modelo
    assert resultado.skipped == {"TESTE_EVENT": 2}
    assert resultado.skipped_total == 2 and resultado.counts.created == 1
    motivos = [r.reason for r in caplog.records if r.getMessage() == "item_skipped"]
    assert motivos == ["TESTE_EVENT", "TESTE_EVENT"]
    assert admin_conn.execute("SELECT created, unchanged FROM ingestion_run").fetchone() == (1, 2)
    assert contar(admin_conn, "event_source_link") == [1]


def test_evento_sem_sessao_futura_e_skipped_com_motivo(
    writer_conn, make_settings, sympla_html, city_id
):
    llm = FakeLlm()
    fetcher = sympla_fetcher(sympla_html, (GOUVEA, "evento_gouvea.html"))
    resultado = executar(writer_conn, make_settings, llm, fetcher)
    assert resultado.skipped == {"NO_FUTURE_SESSION": 1}
    assert llm.prompts == []


def test_cidade_inexistente(writer_conn, admin_conn, make_settings, city_id):
    fetcher = FakeFetcher({}, {})
    with pytest.raises(CityNotFound):
        executar(writer_conn, make_settings, FakeLlm(), fetcher, state_uf="xx", city_slug="nada")
    assert fetcher.listing_calls == []
    assert contar(admin_conn, "ingestion_run") == [0]


def test_cidade_inativa(writer_conn, admin_conn, make_settings, inactive_city_id):
    fetcher = FakeFetcher({}, {})
    with pytest.raises(CityInactive):
        executar(
            writer_conn,
            make_settings,
            FakeLlm(),
            fetcher,
            state_uf="RJ",
            city_slug="rio-de-janeiro",
        )
    assert fetcher.listing_calls == []
    assert contar(admin_conn, "ingestion_run") == [0]


def test_fonte_inativa(writer_conn, admin_conn, make_settings, city_id):
    admin_conn.execute("UPDATE source SET active = false WHERE code = 'sympla'")
    try:
        with pytest.raises(SourceNotFound):
            executar(writer_conn, make_settings, FakeLlm(), FakeFetcher({}, {}))
    finally:
        admin_conn.execute("UPDATE source SET active = true WHERE code = 'sympla'")
    assert contar(admin_conn, "ingestion_run") == [0]


def test_dry_run_nao_grava_e_o_banco_recusa_escrita(
    writer_conn, admin_conn, make_settings, sympla_html, city_id
):
    llm = FakeLlm(structured=[LLM_AGATHA, LLM_GRATUITO])
    fetcher = sympla_fetcher(
        sympla_html, (AGATHA, "evento_agatha.html"), (GRATUITO, "evento_gratuito.html")
    )
    resultado = executar(writer_conn, make_settings, llm, fetcher, dry_run=True)
    assert resultado.run_id is None
    assert contar(admin_conn, "event", "venue", "event_source_link", "ingestion_run") == [
        0,
        0,
        0,
        0,
    ]
    linhas = [json.loads(linha) for linha in resultado.stdout.splitlines()]
    assert [linha["external_id"] for linha in linhas] == [AGATHA[1], GRATUITO[1]]
    assert linhas[0]["action"] == "create" and linhas[0]["category"] == "teatro"
    assert linhas[0]["venue"] == {"would_create": "Fundação Maria Luisa e Oscar Americano"}
    assert linhas[0]["event"]["price_min_cents"] == 10000
    with pytest.raises(psycopg.errors.ReadOnlySqlTransaction):
        writer_conn.execute("INSERT INTO venue (city_id, name) VALUES (1, 'x')")


# ---- fluxo com uma Source falsa, sem nenhuma referência à Sympla ----


class FakeSource:
    """Página = JSON com título e preço; listagem = linhas `id|slug`."""

    code = "sympla"  # só para reaproveitar a linha de source já semeada no banco

    def listing_url(self, state_uf, city_slug, page=1):
        return f"https://fake.test/{city_slug}-{state_uf}?page={page}"

    def parse_listing(self, html):
        pares = [linha.split("|") for linha in html.splitlines() if linha]
        return [DiscoveredItem(i, f"https://fake.test/e/{i}", s) for i, s in pares]

    def structured_data(self, html, timezone):
        dados = json.loads(html)
        return StructuredEvent(
            title=dados["title"],
            description="d",
            sessions=[Session(starts_at=datetime(2026, 12, 1, 20, tzinfo=timezone))],
            price_min_cents=dados["price"],
            price_max_cents=dados["price"],
            source_category="teatro",
            is_cultural=True,
        )

    def pricing_text(self, html):
        return str(json.loads(html)["price"])

    def hash_input(self, html):
        return {"next": json.loads(html), "ld": {}, "pricing": self.pricing_text(html)}

    def page_text(self, html):
        return html


def pagina(titulo: str, preco: int = 1000) -> str:
    return json.dumps({"title": titulo, "price": preco})


def fake_fetcher(n_eventos: int, por_pagina: int | None = None, **kw) -> FakeFetcher:
    todos = [f"{i}|evento-{i}" for i in range(1, n_eventos + 1)]
    por_pagina = por_pagina or n_eventos
    listagens = {}
    for numero, inicio in enumerate(range(0, n_eventos, por_pagina), start=1):
        listagens[f"https://fake.test/sao-paulo-SP?page={numero}"] = "\n".join(
            todos[inicio : inicio + por_pagina]
        )
    events = {
        f"https://fake.test/e/{i}": pagina(f"Peça número {i}") for i in range(1, n_eventos + 1)
    }
    return FakeFetcher(listagens, events, **kw)


def test_ordem_das_etapas_e_triagem_encerra_o_item(
    writer_conn, make_settings, city_id, monkeypatch
):
    passos: list[str] = []
    for nome in ("extract_event", "map_event", "plan_item", "write_item"):
        original = getattr(graph, nome)

        def espiao(*args, _nome=nome, _original=original, **kwargs):
            passos.append(_nome)
            return _original(*args, **kwargs)

        monkeypatch.setattr(graph, nome, espiao)
    source = FakeSource()
    original_hash = source.hash_input
    source.hash_input = lambda html: passos.append("triage") or original_hash(html)
    fetcher = fake_fetcher(1)
    original_event = fetcher.get_event
    fetcher.get_event = lambda url: passos.append("fetch") or original_event(url)

    executar(writer_conn, make_settings, FakeLlm(), fetcher, source=source)
    assert passos == ["fetch", "triage", "extract_event", "map_event", "plan_item", "write_item"]

    passos.clear()
    executar(writer_conn, make_settings, FakeLlm(), fetcher, source=source)
    assert passos == ["fetch", "triage"]  # hash igual: encerra sem extrair, mapear ou gravar


def test_limit_limita_os_itens(writer_conn, admin_conn, make_settings, city_id):
    fetcher = fake_fetcher(8)
    resultado = executar(
        writer_conn, make_settings, FakeLlm(), fetcher, source=FakeSource(), limit=3
    )
    assert resultado.counts.created == 3
    assert len(fetcher.event_calls) == 3
    assert contar(admin_conn, "event") == [3]


def test_limit_maior_que_a_pagina_busca_a_proxima(writer_conn, make_settings, city_id):
    fetcher = fake_fetcher(6, por_pagina=2)
    resultado = executar(
        writer_conn, make_settings, FakeLlm(), fetcher, source=FakeSource(), limit=5
    )
    assert resultado.counts.created == 5
    assert len(fetcher.listing_calls) == 3


def test_limite_de_paginas_por_execucao(writer_conn, admin_conn, make_settings, city_id):
    fetcher = fake_fetcher(8, max_pages=4)  # 1 listagem + 3 eventos
    resultado = executar(writer_conn, make_settings, FakeLlm(), fetcher, source=FakeSource())
    assert resultado.counts.created == 3
    assert len(fetcher.listing_calls) + len(fetcher.event_calls) == 4
    assert contar(admin_conn, "event") == [3]


def test_falha_de_um_evento_nao_interrompe_os_outros(
    writer_conn, admin_conn, make_settings, city_id
):
    fetcher = fake_fetcher(3)
    fetcher.events["https://fake.test/e/2"] = FetchFailed("timeout")
    resultado = executar(writer_conn, make_settings, FakeLlm(), fetcher, source=FakeSource())
    assert (resultado.counts.created, resultado.counts.failed) == (2, 1)
    assert resultado.status == "PARTIAL"
    assert resultado.errors[0].external_id == "2"


class ExplodingSource(FakeSource):
    """Levanta `error` ao processar o evento cujo título termina em `boom_id`."""

    def __init__(self, boom_id: str, error: Exception, where: str = "structured_data"):
        self.boom_id, self.error, self.where = boom_id, error, where

    def structured_data(self, html, timezone):
        if self.where == "structured_data" and json.loads(html)["title"].endswith(self.boom_id):
            raise self.error
        return super().structured_data(html, timezone)

    def hash_input(self, html):
        if self.where == "hash_input" and json.loads(html)["title"].endswith(self.boom_id):
            raise self.error
        return super().hash_input(html)


@pytest.mark.parametrize("where", ["structured_data", "hash_input"])
def test_excecao_inesperada_falha_so_o_item_e_mascara_segredos(
    writer_conn, admin_conn, make_settings, city_id, where
):
    segredo = "sk-ant-chave-secreta-1234"  # a mesma de make_settings
    source = ExplodingSource("2", KeyError(f"campo ausente, chave {segredo}"), where)
    resultado = executar(writer_conn, make_settings, FakeLlm(), fake_fetcher(3), source=source)
    assert (resultado.counts.created, resultado.counts.failed) == (2, 1)
    assert resultado.status == "PARTIAL"
    (erro,) = resultado.errors
    assert erro.external_id == "2"
    assert erro.message.startswith("ITEM_FAILED: KeyError: ")
    assert segredo not in erro.message and "***" in erro.message
    gravado = admin_conn.execute("SELECT status, failed, errors FROM ingestion_run").fetchone()
    assert gravado[0] == "PARTIAL" and gravado[1] == 1
    assert gravado[2][0]["message"] == erro.message
    assert contar(admin_conn, "event") == [2]


def test_excecao_inesperada_no_mapeamento_e_na_gravacao(
    writer_conn, admin_conn, make_settings, city_id, monkeypatch
):
    original_map, original_write = graph.map_event, graph.write_item
    chamadas = {"map": 0, "write": 0}

    def map_quebrado(*args, **kwargs):
        chamadas["map"] += 1
        if chamadas["map"] == 1:
            raise ValueError("quebrou aqui")
        return original_map(*args, **kwargs)

    def write_quebrado(*args, **kwargs):
        chamadas["write"] += 1
        if chamadas["write"] == 1:
            raise AttributeError("gravacao quebrou")
        return original_write(*args, **kwargs)

    monkeypatch.setattr(graph, "map_event", map_quebrado)
    monkeypatch.setattr(graph, "write_item", write_quebrado)
    resultado = executar(
        writer_conn, make_settings, FakeLlm(), fake_fetcher(3), source=FakeSource()
    )
    assert (resultado.counts.created, resultado.counts.failed) == (1, 2)
    mensagens = sorted(e.message for e in resultado.errors)
    assert mensagens == [
        "ITEM_FAILED: AttributeError: gravacao quebrou",
        "ITEM_FAILED: ValueError: quebrou aqui",
    ]
    assert contar(admin_conn, "event") == [1]


def test_excecao_inesperada_ao_baixar_um_evento(writer_conn, make_settings, city_id):
    fetcher = fake_fetcher(3)
    fetcher.events["https://fake.test/e/1"] = RuntimeError("navegador caiu")
    resultado = executar(writer_conn, make_settings, FakeLlm(), fetcher, source=FakeSource())
    assert (resultado.counts.created, resultado.counts.failed) == (2, 1)
    assert resultado.errors[0].message == "ITEM_FAILED: RuntimeError: navegador caiu"
    assert len(fetcher.event_calls) == 3


def test_db_unavailable_aborta_a_execucao(writer_conn, admin_conn, make_settings, city_id):
    source = ExplodingSource("2", DbUnavailable("banco caiu"))
    with pytest.raises(DbUnavailable):
        executar(writer_conn, make_settings, FakeLlm(), fake_fetcher(3), source=source)
    assert admin_conn.execute("SELECT status FROM ingestion_run").fetchone() == ("FAILED",)
    assert contar(admin_conn, "event") == [1]  # o item 3 nem foi tentado


def test_llm_unavailable_aborta_a_execucao(writer_conn, admin_conn, make_settings, city_id):
    source = ExplodingSource("1", LlmUnavailable("modelo caiu"))
    with pytest.raises(LlmUnavailable):
        executar(writer_conn, make_settings, FakeLlm(), fake_fetcher(3), source=source)
    assert admin_conn.execute("SELECT status FROM ingestion_run").fetchone() == ("FAILED",)
    assert contar(admin_conn, "event") == [0]


def test_erro_da_listagem_aborta_mesmo_com_excecao_inesperada(
    writer_conn, admin_conn, make_settings, city_id
):
    class ListagemQuebrada(FakeSource):
        def parse_listing(self, html):
            raise RuntimeError("listagem ilegivel")

    with pytest.raises(RuntimeError):
        executar(writer_conn, make_settings, FakeLlm(), fake_fetcher(2), source=ListagemQuebrada())
    assert admin_conn.execute("SELECT status FROM ingestion_run").fetchone() == ("FAILED",)


# ---- contabilidade dos itens e log final ----


def finais(caplog):
    return [r for r in caplog.records if r.getMessage() == "run_finished"]


def itens_do_final(final):
    return {
        chave: getattr(final, chave)
        for chave in (
            "items_discovered",
            "items_created",
            "items_updated",
            "items_unchanged",
            "items_skipped",
            "items_failed",
            "items_pending",
        )
    }


def cenario_de_cinco(fetcher_events: dict):
    ids = [
        ("1", "evento-1"),
        ("2", "evento-2"),
        ("3", "evento-3"),
        ("4", "evento-teste-4"),
        ("5", "evento-5"),
    ]
    listagem_texto = "\n".join(f"{i}|{s}" for i, s in ids)
    events = {f"https://fake.test/e/{i}": pagina(f"Peça {i}") for i, _ in ids}
    events["https://fake.test/e/2"] = pagina("Peça 2", 2000)  # mudou o preço
    events["https://fake.test/e/5"] = FetchFailed("timeout")
    events.update(fetcher_events)
    return FakeFetcher({"https://fake.test/sao-paulo-SP?page=1": listagem_texto}, events)


def preparar_dois_itens(writer_conn, make_settings):
    previa = FakeFetcher(
        {"https://fake.test/sao-paulo-SP?page=1": "1|evento-1\n2|evento-2"},
        {"https://fake.test/e/1": pagina("Peça 1"), "https://fake.test/e/2": pagina("Peça 2")},
    )
    executar(writer_conn, make_settings, FakeLlm(), previa, source=FakeSource())


def test_cinco_itens_cada_um_em_um_balde_e_run_finished(
    writer_conn, admin_conn, make_settings, city_id, caplog
):
    preparar_dois_itens(writer_conn, make_settings)
    caplog.clear()
    fetcher = cenario_de_cinco({})
    with caplog.at_level("INFO", logger="cultural_events_agent"):
        resultado = executar(writer_conn, make_settings, FakeLlm(), fetcher, source=FakeSource())
    (final,) = finais(caplog)
    assert itens_do_final(final) == {
        "items_discovered": 5,
        "items_created": 1,  # 3
        "items_updated": 1,  # 2 (preço novo)
        "items_unchanged": 1,  # 1
        "items_skipped": 1,  # 4 (teste)
        "items_failed": 1,  # 5 (timeout)
        "items_pending": 0,
    }
    assert final.status == "PARTIAL" and final.dry_run is False and final.run_id == resultado.run_id
    assert final.items_skipped_teste_event == 1
    assert resultado.discovered == 5 and resultado.pending == 0
    # o ingestion_run soma os skipped em unchanged
    assert admin_conn.execute(
        "SELECT created, updated, unchanged, failed FROM ingestion_run WHERE id = %s",
        (resultado.run_id,),
    ).fetchone() == (1, 1, 2, 1)
    soma = sum(
        itens_do_final(final)[k]
        for k in itens_do_final(final)
        if k not in ("items_discovered", "items_pending")
    )
    assert soma == final.items_discovered


def test_cinco_itens_no_dry_run_tambem_fecham_em_baldes(
    writer_conn, admin_conn, make_settings, city_id, caplog
):
    preparar_dois_itens(writer_conn, make_settings)
    antes = contar(admin_conn, "event", "venue", "event_source_link", "ingestion_run")
    caplog.clear()
    with caplog.at_level("INFO", logger="cultural_events_agent"):
        resultado = executar(
            writer_conn,
            make_settings,
            FakeLlm(),
            cenario_de_cinco({}),
            source=FakeSource(),
            dry_run=True,
        )
    (final,) = finais(caplog)
    assert final.dry_run is True and final.run_id == "-" and resultado.run_id is None
    assert itens_do_final(final) == {
        "items_discovered": 5,
        "items_created": 1,
        "items_updated": 1,
        "items_unchanged": 1,
        "items_skipped": 1,
        "items_failed": 1,
        "items_pending": 0,
    }
    acoes = [json.loads(linha)["action"] for linha in resultado.stdout.splitlines()]
    assert sorted(acoes) == ["create", "failed", "skipped", "unchanged", "update"]
    assert contar(admin_conn, "event", "venue", "event_source_link", "ingestion_run") == antes


def test_run_finished_quando_a_execucao_e_abortada(
    writer_conn, admin_conn, make_settings, city_id, caplog
):
    source = ExplodingSource("2", DbUnavailable("banco caiu"))
    with caplog.at_level("INFO", logger="cultural_events_agent"), pytest.raises(DbUnavailable):
        executar(writer_conn, make_settings, FakeLlm(), fake_fetcher(3), source=source)
    (final,) = finais(caplog)
    assert final.status == "FAILED" and final.error_code == "DB_UNAVAILABLE"
    assert (final.items_discovered, final.items_created, final.items_pending) == (3, 1, 2)


def test_run_finished_quando_a_validacao_inicial_falha(writer_conn, make_settings, caplog):
    with caplog.at_level("INFO", logger="cultural_events_agent"), pytest.raises(CityNotFound):
        executar(
            writer_conn,
            make_settings,
            FakeLlm(),
            FakeFetcher({}, {}),
            state_uf="xx",
            city_slug="nada",
        )
    (final,) = finais(caplog)
    assert final.status == "FAILED" and final.error_code == "CITY_NOT_FOUND"
    assert (final.items_discovered, final.items_created, final.items_pending) == (0, 0, 0)


def test_itens_nao_processados_pelo_limite_de_paginas_ficam_pendentes(
    writer_conn, make_settings, city_id, caplog
):
    with caplog.at_level("INFO", logger="cultural_events_agent"):
        executar(
            writer_conn, make_settings, FakeLlm(), fake_fetcher(8, max_pages=4), source=FakeSource()
        )
    (final,) = finais(caplog)
    assert (final.items_discovered, final.items_created, final.items_pending) == (8, 3, 5)


# ---- EXTRACTION_INVALID: detalhes e resposta bruta ----


def test_extraction_invalid_traz_loc_type_e_msg_sem_o_input(
    writer_conn, make_settings, sympla_html, city_id
):
    llm = FakeLlm(structured={"price_min_cents": "caro-demais"})
    fetcher = sympla_fetcher(sympla_html, (AGATHA, "evento_agatha.html"))
    resultado = executar(writer_conn, make_settings, llm, fetcher)
    (erro,) = resultado.errors
    assert "EXTRACTION_INVALID" in erro.message
    assert "price_min_cents [int_parsing]" in erro.message
    assert "caro-demais" not in erro.message


def test_debug_grava_a_resposta_bruta_do_modelo(
    writer_conn, make_settings, sympla_html, city_id, tmp_path
):
    bruta = {"price_min_cents": "caro", "category_slug": "teatro"}
    llm = FakeLlm(structured=bruta)
    fetcher = sympla_fetcher(sympla_html, (AGATHA, "evento_agatha.html"))
    debug = tmp_path / ".debug"
    executar(writer_conn, make_settings, llm, fetcher, debug_dir=debug)
    arquivo = debug / f"{AGATHA[1]}.json"
    dados = json.loads(arquivo.read_text(encoding="utf-8"))
    assert dados["external_id"] == AGATHA[1] and dados["raw"] == bruta
    assert "EXTRACTION_INVALID" not in dados["error"] and "price_min_cents" in dados["error"]


def test_debug_tambem_grava_quando_o_item_final_e_invalido(
    writer_conn, make_settings, sympla_html, city_id, tmp_path
):
    bruta = {"category_slug": "teatro", "price_min_cents": 2000, "price_max_cents": 1000}
    fetcher = sympla_fetcher(sympla_html, (AGATHA, "evento_agatha.html"))
    executar(writer_conn, make_settings, FakeLlm(structured=bruta), fetcher, debug_dir=tmp_path)
    dados = json.loads((tmp_path / f"{AGATHA[1]}.json").read_text(encoding="utf-8"))
    assert dados["raw"] == bruta and "value_error" in dados["error"]


def test_sem_debug_nada_e_gravado(
    writer_conn, make_settings, sympla_html, city_id, tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    llm = FakeLlm(structured={"price_min_cents": "caro"})
    executar(
        writer_conn, make_settings, llm, sympla_fetcher(sympla_html, (AGATHA, "evento_agatha.html"))
    )
    assert not (tmp_path / ".debug").exists()


def test_debug_mascara_segredos_na_resposta_bruta(
    writer_conn, make_settings, sympla_html, city_id, tmp_path
):
    segredo = "sk-ant-chave-secreta-1234"
    bruta = {"price_min_cents": "caro", "description": f"vazou {segredo}"}
    fetcher = sympla_fetcher(sympla_html, (AGATHA, "evento_agatha.html"))
    executar(writer_conn, make_settings, FakeLlm(structured=bruta), fetcher, debug_dir=tmp_path)
    texto = (tmp_path / f"{AGATHA[1]}.json").read_text(encoding="utf-8")
    assert segredo not in texto and "vazou ***" in texto


class DoisCasos(FakeSource):
    """Item 1 precisa do modelo (categoria desconhecida); o 2 é incoerente sem chamá-lo."""

    def structured_data(self, html, timezone):
        evento = super().structured_data(html, timezone)
        if evento.title.endswith("1"):
            return evento.model_copy(update={"source_category": "nao-existe"})
        return evento.model_copy(update={"price_min_cents": 2000, "price_max_cents": 1000})


def test_resposta_bruta_de_outro_item_nao_vaza_para_o_seguinte(
    writer_conn, make_settings, city_id, tmp_path
):
    llm = FakeLlm(structured={"price_min_cents": "caro"})
    fetcher = fake_fetcher(2)
    resultado = executar(
        writer_conn, make_settings, llm, fetcher, source=DoisCasos(), debug_dir=tmp_path
    )
    assert resultado.counts.failed == 2 and len(llm.prompts) == 1  # o item 2 não chamou o modelo
    assert [p.name for p in tmp_path.iterdir()] == ["1.json"]


# ---- filtro de eventos não culturais ----

NAO_CULTURAL = {
    "category_slug": "outros",
    "is_cultural": False,
    "cultural_reason": "Workshop técnico de programação",
}


def skips(caplog, mensagem="item_skipped"):
    return [r for r in caplog.records if r.getMessage() == mensagem]


def test_evento_nao_cultural_e_skipped_sem_gravar_nada(
    writer_conn, admin_conn, make_settings, sympla_html, city_id, caplog
):
    llm = FakeLlm(structured=NAO_CULTURAL)
    fetcher = sympla_fetcher(sympla_html, (GRATUITO, "evento_gratuito.html"))
    with caplog.at_level("INFO", logger="cultural_events_agent"):
        resultado = executar(writer_conn, make_settings, llm, fetcher)
    assert resultado.skipped == {"NOT_CULTURAL": 1}
    assert (resultado.counts.created, resultado.counts.failed) == (0, 0)
    assert contar(admin_conn, "event", "venue", "event_source_link", "occurrence") == [0, 0, 0, 0]
    (registro,) = skips(caplog)
    assert registro.reason == "NOT_CULTURAL"
    assert registro.detail == "Workshop técnico de programação"
    (final,) = finais(caplog)
    assert final.items_skipped == 1 and final.items_skipped_not_cultural == 1


def test_evento_cultural_e_gravado_com_o_mesmo_criterio(
    writer_conn, admin_conn, make_settings, sympla_html, city_id
):
    llm = FakeLlm(
        structured=[
            {**LLM_AGATHA, "is_cultural": True, "cultural_reason": "Conversa literária"},
            NAO_CULTURAL,
        ]
    )
    fetcher = sympla_fetcher(
        sympla_html, (AGATHA, "evento_agatha.html"), (GRATUITO, "evento_gratuito.html")
    )
    resultado = executar(writer_conn, make_settings, llm, fetcher)
    assert (resultado.counts.created, resultado.skipped) == (1, {"NOT_CULTURAL": 1})
    assert admin_conn.execute("SELECT title FROM event").fetchall() == [
        ("VAMOS FALAR DE AGATHA CHRISTIE & LUIZ ALFREDO GARCIA ROZA",)
    ]
    assert len(llm.prompts) == 2  # uma chamada por item, sem chamada extra para classificar


def test_dry_run_usa_o_mesmo_criterio(writer_conn, admin_conn, make_settings, sympla_html, city_id):
    llm = FakeLlm(structured=[{**LLM_AGATHA, "is_cultural": True}, NAO_CULTURAL])
    fetcher = sympla_fetcher(
        sympla_html, (AGATHA, "evento_agatha.html"), (GRATUITO, "evento_gratuito.html")
    )
    resultado = executar(writer_conn, make_settings, llm, fetcher, dry_run=True)
    linhas = {j["external_id"]: j for j in map(json.loads, resultado.stdout.splitlines())}
    assert linhas[AGATHA[1]]["action"] == "create"
    nao_cultural = linhas[GRATUITO[1]]
    assert nao_cultural["action"] == "skipped" and nao_cultural["reason"] == "NOT_CULTURAL"
    assert nao_cultural["detail"] == "Workshop técnico de programação"
    assert contar(admin_conn, "event", "venue", "event_source_link", "ingestion_run") == [
        0,
        0,
        0,
        0,
    ]


def test_evento_ja_gravado_reclassificado_nao_e_alterado_so_logado(
    writer_conn, admin_conn, make_settings, sympla_html, city_id, caplog
):
    llm = FakeLlm(structured=[{**LLM_AGATHA, "is_cultural": True}, NAO_CULTURAL])
    executar(
        writer_conn, make_settings, llm, sympla_fetcher(sympla_html, (AGATHA, "evento_agatha.html"))
    )
    event_id, hash_antes = admin_conn.execute(
        "SELECT e.id, l.content_hash FROM event e JOIN event_source_link l ON l.event_id = e.id"
    ).fetchone()
    antes = admin_conn.execute(
        "SELECT status, version, price_min_cents, updated_at FROM event"
    ).fetchone()

    mudou = sympla_fetcher(sympla_html, (AGATHA, "evento_agatha_preco110.html"))
    with caplog.at_level("INFO", logger="cultural_events_agent"):
        resultado = executar(writer_conn, make_settings, llm, mudou)

    assert resultado.skipped == {"NOT_CULTURAL": 1} and resultado.counts.updated == 0
    assert contar(admin_conn, "event", "event_source_link") == [1, 1]  # nada apagado
    assert (
        admin_conn.execute(
            "SELECT status, version, price_min_cents, updated_at FROM event"
        ).fetchone()
        == antes
    )  # nem despublicado nem alterado
    assert admin_conn.execute(
        "SELECT event_id, content_hash FROM event_source_link"
    ).fetchone() == (
        event_id,
        hash_antes,
    )
    (aviso,) = skips(caplog, "existing_event_not_cultural")
    assert aviso.levelname == "WARNING" and aviso.event_id == event_id
    assert aviso.external_id == AGATHA[1] and aviso.detail == "Workshop técnico de programação"


def test_evento_novo_nao_cultural_nao_gera_aviso_de_evento_existente(
    writer_conn, make_settings, sympla_html, city_id, caplog
):
    fetcher = sympla_fetcher(sympla_html, (GRATUITO, "evento_gratuito.html"))
    with caplog.at_level("INFO", logger="cultural_events_agent"):
        executar(writer_conn, make_settings, FakeLlm(structured=NAO_CULTURAL), fetcher)
    assert skips(caplog, "existing_event_not_cultural") == []


def test_limitacao_conhecida_nao_culturais_sao_reavaliados_a_cada_execucao(
    writer_conn, admin_conn, make_settings, sympla_html, city_id
):
    """Não há vínculo gravado para itens não culturais: cada execução baixa e chama o modelo."""
    llm = FakeLlm(structured=NAO_CULTURAL)
    fetcher = sympla_fetcher(sympla_html, (GRATUITO, "evento_gratuito.html"))
    executar(writer_conn, make_settings, llm, fetcher)
    executar(writer_conn, make_settings, llm, fetcher)
    assert len(llm.prompts) == 2
    assert len(fetcher.event_calls) == 2
    assert contar(admin_conn, "event_source_link") == [0]


# ---- 429 seguidos interrompem a execução ----


class FetcherComRateLimit(FakeFetcher):
    """Eventos cujo id está em `limitados` respondem 429."""

    def __init__(self, *args, limitados=(), **kwargs):
        super().__init__(*args, **kwargs)
        self.limitados = set(limitados)

    def get_event(self, url):
        from cultural_events_agent.errors import RateLimited

        if url.rsplit("/", 1)[1] in self.limitados:
            self.event_calls.append(url)
            raise RateLimited("HTTP 429", retry_after=10)
        return super().get_event(url)


def fetcher_429(n, limitados):
    base = fake_fetcher(n)
    return FetcherComRateLimit(base.listings, base.events, limitados=limitados)


def test_tres_429_seguidos_interrompem_a_execucao_e_fecham_como_failed(
    writer_conn, admin_conn, make_settings, city_id, caplog
):
    from cultural_events_agent.errors import RateLimited

    fetcher = fetcher_429(8, limitados={"3", "4", "5", "6", "7", "8"})
    with caplog.at_level("INFO", logger="cultural_events_agent"), pytest.raises(RateLimited) as exc:
        executar(writer_conn, make_settings, FakeLlm(), fetcher, source=FakeSource())
    assert exc.value.exit_code == 11 and "execução interrompida" in exc.value.message
    assert len(fetcher.event_calls) == 5  # 1 e 2 ok; 3, 4 e 5 (429); nada além
    (final,) = finais(caplog)
    assert final.status == "FAILED" and final.error_code == "RATE_LIMITED"
    assert (final.items_created, final.items_failed, final.items_pending) == (2, 3, 3)
    assert admin_conn.execute("SELECT status, created, failed FROM ingestion_run").fetchone() == (
        "FAILED",
        2,
        3,
    )
    assert contar(admin_conn, "event") == [2]  # o que já tinha sido gravado fica


def test_429_alternado_com_sucesso_nao_interrompe(writer_conn, make_settings, city_id):
    fetcher = fetcher_429(7, limitados={"2", "3", "5", "6"})
    resultado = executar(writer_conn, make_settings, FakeLlm(), fetcher, source=FakeSource())
    assert (resultado.counts.created, resultado.counts.failed) == (3, 4)
    assert len(fetcher.event_calls) == 7
    assert resultado.status == "PARTIAL"


def test_falha_comum_zera_a_sequencia_de_429(writer_conn, make_settings, city_id):
    fetcher = fetcher_429(6, limitados={"1", "2", "4", "5"})
    fetcher.events["https://fake.test/e/3"] = FetchFailed("timeout")  # quebra a sequência
    resultado = executar(writer_conn, make_settings, FakeLlm(), fetcher, source=FakeSource())
    assert len(fetcher.event_calls) == 6 and resultado.counts.failed == 5


def test_o_429_do_item_aparece_com_o_codigo_certo_em_errors(writer_conn, make_settings, city_id):
    fetcher = fetcher_429(2, limitados={"1"})
    resultado = executar(writer_conn, make_settings, FakeLlm(), fetcher, source=FakeSource())
    assert resultado.errors[0].message.startswith("RATE_LIMITED: HTTP 429")
    assert resultado.counts.created == 1
