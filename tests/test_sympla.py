from zoneinfo import ZoneInfo

from cultural_events_agent.sources.sympla import SymplaSource

SP = ZoneInfo("America/Sao_Paulo")
source = SymplaSource()


def test_url_da_listagem():
    assert source.listing_url("SP", "sao-paulo") == "https://www.sympla.com.br/eventos/sao-paulo-sp"
    assert source.listing_url("SP", "sao-paulo", 2).endswith("/eventos/sao-paulo-sp?page=2")


def test_links_da_listagem_do_servidor_sem_duplicados(sympla_html):
    items = source.parse_listing(sympla_html("listagem_sp.html"))
    ids = [i.external_id for i in items]
    assert len(ids) == len(set(ids)) == 14
    agatha = next(i for i in items if i.external_id == "3298539")
    assert agatha.url == (
        "https://www.sympla.com.br/evento/vamos-falar-de-agatha-christie-luiz-alfredo-garcia-roza/3298539"
    )
    assert agatha.slug == "vamos-falar-de-agatha-christie-luiz-alfredo-garcia-roza"
    assert all("?" not in i.url for i in items)
    assert "evento-teste-565" in {i.slug for i in items}


def test_links_no_formato_do_dom_renderizado():
    html = (
        '<a href="/evento/show-a/11?mp_rloc=SP&mp_rtil=x">a</a>'
        '<a href="https://www.sympla.com.br/evento/show-b/22">b</a>'
        '<a href="/evento/show-a/11">repetido</a><a href="/ajuda">ajuda</a>'
    )
    items = source.parse_listing(html)
    assert [(i.external_id, i.slug) for i in items] == [("11", "show-a"), ("22", "show-b")]
    assert items[0].url == "https://www.sympla.com.br/evento/show-a/11"


def test_listagem_sem_eventos():
    assert source.parse_listing("<html><body>nada</body></html>") == []


def test_dados_estruturados_do_evento(sympla_html):
    event = source.structured_data(sympla_html("evento_agatha.html"), SP)
    assert event.title == "VAMOS FALAR DE AGATHA CHRISTIE & LUIZ ALFREDO GARCIA ROZA"
    (session,) = event.sessions
    assert session.starts_at.isoformat() == "2026-12-12T10:30:00-03:00"
    assert session.ends_at.isoformat() == "2026-12-12T13:30:00-03:00"
    assert event.venue_name == "Fundação Maria Luisa e Oscar Americano"
    assert event.address_line == "Avenida Morumbi 4077"
    assert event.neighborhood == "Morumbi"
    assert event.postal_code == "05650-000"
    assert round(event.latitude, 4) == -23.6013
    assert event.source_category == "sociedade-e-cultura"
    assert event.image_url.startswith("https://images.sympla.com.br/")
    assert event.ticket_url.endswith(
        "/evento/vamos-falar-de-agatha-christie-luiz-alfredo-garcia-roza/3298539"
    )
    assert event.public is True
    assert event.price_min_cents is None  # o preço só existe no texto de ingressos


def test_descricao_completa_vem_do_next_data(sympla_html):
    event = source.structured_data(sympla_html("evento_agatha.html"), SP)
    assert event.description.startswith("Vamos falar de")
    assert not event.description.endswith(("...", "…"))
    assert "&nbsp;" not in event.description and "&amp;" not in event.description


def test_descricao_truncada_do_json_ld_nao_e_usada():
    html = """<script type="application/ld+json">{"@type": "Event", "name": "X",
        "description": "Um texto que termina em...",
        "startDate": "2026-12-12T10:00:00-03:00"}</script>"""
    event = source.structured_data(html, SP)
    assert event.description is None
    assert event.title == "X"


def test_horario_sem_fuso_usa_o_fuso_da_cidade():
    html = """<script id="__NEXT_DATA__" type="application/json">{"props": {"pageProps":
      {"hydrationData": {"eventHydration": {"event": {"name": "Y",
      "startDate": "2026-12-12 10:30:00", "endDate": "2026-12-12 12:00:00"}}}}}}</script>"""
    (session,) = source.structured_data(html, SP).sessions
    assert session.starts_at.isoformat() == "2026-12-12T10:30:00-03:00"
    assert session.ends_at.isoformat() == "2026-12-12T12:00:00-03:00"


def test_local_a_definir_sem_endereco(sympla_html):
    event = source.structured_data(sympla_html("evento_gouvea.html"), SP)
    assert event.venue_name == "Local a definir"
    assert event.address_line is None
    assert event.postal_code is None


def test_evento_privado_nao_e_publico(sympla_html):
    assert source.structured_data(sympla_html("evento_privado.html"), SP).public is False


def test_pagina_sem_dados_estruturados():
    event = source.structured_data("<html><body>nada</body></html>", SP)
    assert event.title is None and event.sessions == []


def test_texto_da_secao_de_ingressos_achado_na_fixture(sympla_html):
    for name in ("evento_agatha.html", "evento_gouvea.html"):
        assert "R$ " in source.pricing_text(sympla_html(name)), name
    text = source.pricing_text(sympla_html("evento_agatha.html"))
    assert "R$ 100,00 (+10,00 taxa)" in text.splitlines()


def test_pagina_sem_secao_de_ingressos():
    assert source.pricing_text("<html><body><p>R$ 10,00 fora da seção</p></body></html>") == ""


def test_texto_da_pagina_ignora_scripts(sympla_html):
    text = source.page_text(sympla_html("evento_agatha.html"))
    assert "VAMOS FALAR DE AGATHA CHRISTIE" in text
    assert "__NEXT_DATA__" not in text and "buildId" not in text


def test_hash_input_traz_so_dados_relevantes(sympla_html):
    data = source.hash_input(sympla_html("evento_agatha.html"))
    assert set(data) == {"next", "ld", "pricing"}
    assert data["next"]["id"] == 3298539
    assert "cardPreviousView" not in data["next"] and "buildId" not in data["next"]
    assert data["ld"]["@type"] == "Event"
    assert "R$ 100,00" in data["pricing"]


def test_hash_input_ignora_campos_volateis(sympla_html):
    base = source.hash_input(sympla_html("evento_agatha.html"))
    assert source.hash_input(sympla_html("evento_agatha_volatil.html")) == base


def test_hash_input_muda_com_o_preco(sympla_html):
    base = source.hash_input(sympla_html("evento_agatha.html"))
    assert source.hash_input(sympla_html("evento_agatha_preco110.html")) != base
