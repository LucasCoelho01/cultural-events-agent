from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from cultural_events_agent.errors import ExtractionInvalid
from cultural_events_agent.extraction import ItemSkipped, extract_event
from cultural_events_agent.llm import FakeLlm
from cultural_events_agent.sources.base import Session, StructuredEvent
from cultural_events_agent.sources.sympla import SymplaSource

SP = ZoneInfo("America/Sao_Paulo")
NOW = datetime(2026, 10, 30, 12, 0, tzinfo=SP)
CATEGORIES = ["shows", "teatro", "exposicoes", "danca", "cinema", "outros"]
URL = "https://www.sympla.com.br/evento/x/1"
source = SymplaSource()


def run(html: str, llm: FakeLlm):
    return extract_event(
        source.structured_data(html, SP),
        llm=llm,
        categories=CATEGORIES,
        page_text=source.page_text(html),
        pricing_text=source.pricing_text(html),
        timezone=SP,
        now=NOW,
        item_url=URL,
    )


def completo(**extra) -> StructuredEvent:
    """Um evento em que os dados estruturados cobrem tudo, inclusive preço e categoria."""
    values = {
        "title": "Peça de teatro",
        "description": "Uma peça.",
        "sessions": [Session(starts_at=datetime(2026, 12, 1, 20, tzinfo=SP))],
        "price_min_cents": 5000,
        "price_max_cents": 8000,
        "source_category": "teatro",
        "venue_name": "Teatro X",
        "is_cultural": True,
    }
    values.update(extra)
    return StructuredEvent(**values)


def extrair(structured: StructuredEvent, llm: FakeLlm):
    return extract_event(
        structured,
        llm=llm,
        categories=CATEGORIES,
        page_text="texto",
        pricing_text="",
        timezone=SP,
        now=NOW,
        item_url=URL,
    )


def test_modelo_completa_preco_e_categoria_do_evento_da_sympla(sympla_html):
    llm = FakeLlm(
        structured={"category_slug": "teatro", "price_min_cents": 10000, "price_max_cents": 11000}
    )
    event = run(sympla_html("evento_agatha.html"), llm)
    assert event.title == "VAMOS FALAR DE AGATHA CHRISTIE & LUIZ ALFREDO GARCIA ROZA"
    assert (event.price_min_cents, event.price_max_cents) == (10000, 11000)
    assert event.category_slug == "teatro"
    assert event.sessions[0].starts_at.isoformat() == "2026-12-12T10:30:00-03:00"
    assert event.venue_name == "Fundação Maria Luisa e Oscar Americano"
    assert len(llm.prompts) == 1
    prompt = llm.prompts[0]
    assert "shows, teatro" in prompt and "R$ 100,00" not in prompt  # nbsp já colapsado
    assert "R$ 100,00 (+10,00 taxa)" in prompt
    assert "price" in prompt.split("Campos que faltam:")[1].splitlines()[0]


def test_modelo_nao_e_chamado_quando_nada_falta():
    llm = FakeLlm()
    event = extrair(completo(), llm)
    assert llm.prompts == []
    assert event.category_slug == "teatro"
    assert event.ticket_url == URL
    assert event.is_free is False


def test_campos_estruturados_prevalecem_sobre_o_modelo():
    llm = FakeLlm(structured={"description": "inventada", "category_slug": "shows"})
    event = extrair(completo(description=None, source_category="sociedade-e-cultura"), llm)
    assert event.description == "inventada"
    assert event.category_slug == "shows"
    assert event.price_min_cents == 5000


def test_evento_gratuito(sympla_html):
    llm = FakeLlm(structured={"category_slug": "outros", "is_free": True})
    event = run(sympla_html("evento_gratuito.html"), llm)
    assert event.is_free is True
    assert not event.price_min_cents
    assert "Gratuito" in llm.prompts[0]


def test_horario_sem_fuso_do_modelo_usa_o_fuso_da_cidade():
    structured = completo(sessions=[], description="d")
    llm = FakeLlm(structured={"sessions": [{"starts_at": "2026-12-12T10:30:00"}]})
    event = extrair(structured, llm)
    assert event.sessions[0].starts_at.isoformat() == "2026-12-12T10:30:00-03:00"


def test_resposta_fora_do_esquema():
    llm = FakeLlm(structured={"price_min_cents": "caro"})
    with pytest.raises(ExtractionInvalid):
        extrair(completo(price_min_cents=None, price_max_cents=None), llm)


def test_preco_incoerente():
    llm = FakeLlm(structured={"price_min_cents": 2000, "price_max_cents": 1000})
    with pytest.raises(ExtractionInvalid, match="item inválido") as exc:
        extrair(completo(price_min_cents=None, price_max_cents=None), llm)
    assert "[value_error]" in exc.value.message
    assert "preço máximo menor que o mínimo" in exc.value.message


def test_gratuito_com_preco_positivo():
    with pytest.raises(ExtractionInvalid):
        extrair(completo(is_free=True), FakeLlm())


def test_fim_antes_do_inicio():
    sessao = Session(
        starts_at=datetime(2026, 12, 1, 20, tzinfo=SP), ends_at=datetime(2026, 12, 1, 18, tzinfo=SP)
    )
    with pytest.raises(ExtractionInvalid):
        extrair(completo(sessions=[sessao]), FakeLlm())


def test_sem_nenhuma_sessao():
    with pytest.raises(ExtractionInvalid):
        extrair(completo(sessions=[]), FakeLlm(structured={"category_slug": "outros"}))


def test_titulo_vazio():
    with pytest.raises(ExtractionInvalid):
        extrair(completo(title="  "), FakeLlm())


def test_evento_ja_iniciado_e_skipped_sem_chamar_o_modelo(sympla_html):
    llm = FakeLlm()
    with pytest.raises(ItemSkipped) as exc:
        run(sympla_html("evento_passado.html"), llm)
    assert exc.value.reason == "NO_FUTURE_SESSION"
    assert llm.prompts == []


def test_evento_privado_e_skipped(sympla_html):
    llm = FakeLlm()
    with pytest.raises(ItemSkipped) as exc:
        run(sympla_html("evento_privado.html"), llm)
    assert exc.value.reason == "EVENT_NOT_PUBLIC"
    assert llm.prompts == []


def test_titulo_com_teste_e_skipped_antes_do_modelo(sympla_html):
    llm = FakeLlm()
    with pytest.raises(ItemSkipped) as exc:
        run(sympla_html("evento_teste_titulo.html"), llm)
    assert exc.value.reason == "TESTE_EVENT"
    assert llm.prompts == []


def test_testemunho_nao_e_skipped(sympla_html):
    llm = FakeLlm(structured={"category_slug": "outros"})
    assert run(sympla_html("evento_testemunho.html"), llm).title == "Testemunho de fé"


# ---- o esquema enviado à API só tem tipos simples; as restrições ficam no NormalizedEvent ----

# Palavras-chave do JSON Schema que a saída estruturada da API não aceita (ou aceita só em parte).
PROIBIDAS = {
    "minimum",
    "maximum",
    "exclusiveMinimum",
    "exclusiveMaximum",
    "multipleOf",
    "minLength",
    "maxLength",
    "pattern",
    "format",
    "minItems",
    "maxItems",
    "uniqueItems",
    "minProperties",
    "maxProperties",
}


def palavras_proibidas(node, caminho="#") -> list[str]:
    """Percorre o JSON Schema olhando só as palavras-chave (nunca os nomes dos campos)."""
    achadas = []
    if isinstance(node, dict):
        for chave, valor in node.items():
            if chave in PROIBIDAS:
                achadas.append(f"{caminho}.{chave}")
            if chave in ("properties", "$defs", "definitions"):
                for nome, sub in valor.items():
                    achadas += palavras_proibidas(sub, f"{caminho}.{chave}.{nome}")
            else:
                achadas += palavras_proibidas(valor, f"{caminho}.{chave}")
    elif isinstance(node, list):
        for indice, sub in enumerate(node):
            achadas += palavras_proibidas(sub, f"{caminho}[{indice}]")
    return achadas


def test_json_schema_do_event_completion_so_tem_tipos_simples():
    from cultural_events_agent.extraction import EventCompletion

    assert palavras_proibidas(EventCompletion.model_json_schema()) == []


def test_o_detector_de_palavras_proibidas_funciona():
    from datetime import datetime

    from pydantic import BaseModel, Field

    class Ruim(BaseModel):
        preco: int = Field(ge=0, le=10)
        quando: datetime
        codigo: str = Field(pattern="^a+$", max_length=3)

    achadas = " ".join(palavras_proibidas(Ruim.model_json_schema()))
    for palavra in ("minimum", "maximum", "format", "pattern", "maxLength"):
        assert palavra in achadas


def test_restricoes_removidas_do_esquema_continuam_valendo_no_normalized_event():
    from cultural_events_agent.extraction import EventCompletion

    # o esquema aceita o valor, mas o item final é recusado
    assert EventCompletion(price_min_cents=-5).price_min_cents == -5
    llm = FakeLlm(structured={"price_min_cents": -5, "price_max_cents": 10})
    with pytest.raises(ExtractionInvalid) as exc:
        extrair(completo(price_min_cents=None, price_max_cents=None), llm)
    assert "price_min_cents [greater_than_equal]" in exc.value.message


def test_data_ilegivel_do_modelo_falha_o_item_com_o_campo():
    structured = completo(sessions=[], description="d")
    llm = FakeLlm(structured={"sessions": [{"starts_at": "amanhã às oito"}]})
    with pytest.raises(ExtractionInvalid) as exc:
        extrair(structured, llm)
    assert "sessions.0.starts_at [datetime_parsing]" in exc.value.message
    assert "amanhã" not in exc.value.message


def test_data_com_fuso_do_modelo_e_preservada():
    structured = completo(sessions=[], description="d")
    llm = FakeLlm(
        structured={
            "sessions": [
                {"starts_at": "2026-12-12T10:30:00-03:00", "ends_at": "2026-12-12T12:00:00-03:00"}
            ]
        }
    )
    (sessao,) = extrair(structured, llm).sessions
    assert sessao.starts_at.isoformat() == "2026-12-12T10:30:00-03:00"
    assert sessao.ends_at.isoformat() == "2026-12-12T12:00:00-03:00"


# ---- filtro de eventos não culturais (na mesma chamada de extração) ----


def test_evento_cultural_segue_o_fluxo_na_mesma_chamada(sympla_html):
    llm = FakeLlm(
        structured={
            "category_slug": "teatro",
            "price_min_cents": 10000,
            "is_cultural": True,
            "cultural_reason": "Conversa literária aberta ao público",
        }
    )
    event = run(sympla_html("evento_agatha.html"), llm)
    assert event.title.startswith("VAMOS FALAR")
    assert len(llm.prompts) == 1  # a classificação não custa chamada extra


def test_modelo_que_omite_is_cultural_nao_descarta_o_evento(sympla_html):
    llm = FakeLlm(structured={"category_slug": "teatro", "price_min_cents": 10000})
    assert run(sympla_html("evento_agatha.html"), llm).title


def test_evento_nao_cultural_vira_skipped_com_o_motivo(sympla_html):
    llm = FakeLlm(
        structured={
            "category_slug": "outros",
            "is_cultural": False,
            "cultural_reason": "Workshop técnico de   programação",
        }
    )
    with pytest.raises(ItemSkipped) as exc:
        run(sympla_html("evento_agatha.html"), llm)
    assert exc.value.reason == "NOT_CULTURAL"
    assert exc.value.detail == "Workshop técnico de programação"
    assert len(llm.prompts) == 1


def test_motivo_longo_e_cortado():
    llm = FakeLlm(structured={"is_cultural": False, "cultural_reason": "x" * 1000})
    with pytest.raises(ItemSkipped) as exc:
        extrair(completo(is_cultural=None), llm)
    assert len(exc.value.detail) == 300


def test_nao_cultural_vence_qualquer_outra_validacao():
    llm = FakeLlm(
        structured={
            "is_cultural": False,
            "cultural_reason": "Curso",
            "price_min_cents": 2000,
            "price_max_cents": 1000,
        }
    )
    with pytest.raises(ItemSkipped) as exc:
        extrair(completo(is_cultural=None, price_min_cents=None, price_max_cents=None), llm)
    assert exc.value.reason == "NOT_CULTURAL"  # e não EXTRACTION_INVALID


def test_prompt_traz_o_criterio_e_a_categoria_da_fonte_como_dica(sympla_html):
    llm = FakeLlm(structured={"category_slug": "outros", "is_cultural": True})
    run(sympla_html("evento_agatha.html"), llm)
    prompt = llm.prompts[0]
    for palavra in ("show, teatro, dança", "networking", "webinar", "esporte competitivo"):
        assert palavra in prompt
    assert "Categoria atribuída pela fonte (só uma dica, não decide): sociedade-e-cultura" in prompt
    assert "cultural" in prompt.split("Campos que faltam:")[1].splitlines()[0]


def test_veredito_da_fonte_dispensa_o_modelo():
    llm = FakeLlm()
    with pytest.raises(ItemSkipped) as exc:
        extrair(completo(is_cultural=False, cultural_reason="Curso de gestão"), llm)
    assert (exc.value.reason, exc.value.detail) == ("NOT_CULTURAL", "Curso de gestão")
    assert llm.prompts == []


def test_sem_veredito_da_fonte_o_modelo_e_chamado():
    llm = FakeLlm(structured={"is_cultural": True})
    extrair(completo(is_cultural=None), llm)
    assert len(llm.prompts) == 1
