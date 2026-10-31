import pytest

from cultural_events_agent.fingerprint import content_hash, is_test_event
from cultural_events_agent.sources.sympla import SymplaSource

source = SymplaSource()


def test_hash_tem_64_caracteres_hexadecimais():
    digest = content_hash({"next": {"id": 1}, "ld": {}, "pricing": "R$ 10,00"})
    assert len(digest) == 64
    int(digest, 16)


def test_hash_nao_depende_da_ordem_das_chaves():
    a = {"next": {"id": 1, "name": "X"}, "ld": {"a": 1, "b": 2}, "pricing": ""}
    b = {"pricing": "", "ld": {"b": 2, "a": 1}, "next": {"name": "X", "id": 1}}
    assert content_hash(a) == content_hash(b)


def test_hash_ignora_espacos_e_entidades_html():
    a = {"next": {"strippedDetail": "Show   de\n  rock &amp; roll"}, "pricing": "R$ 10"}
    b = {"next": {"strippedDetail": "Show de rock & roll"}, "pricing": "R$ 10"}
    assert content_hash(a) == content_hash(b)


def test_hash_muda_com_o_conteudo():
    base = {"next": {"id": 1}, "ld": {}, "pricing": "R$ 10,00"}
    assert content_hash(base) != content_hash({**base, "pricing": "R$ 12,00"})
    assert content_hash(base) != content_hash({**base, "next": {"id": 2}})


def test_hash_incorpora_a_versao_da_regra(monkeypatch):
    raw = {"next": {}, "ld": {}, "pricing": ""}
    atual = content_hash(raw)
    monkeypatch.setattr("cultural_events_agent.fingerprint.HASH_VERSION", 2)
    assert content_hash(raw) != atual


def test_hash_da_fixture_ignora_campo_volatil_e_muda_com_preco(sympla_html):
    base = content_hash(source.hash_input(sympla_html("evento_agatha.html")))
    assert content_hash(source.hash_input(sympla_html("evento_agatha_volatil.html"))) == base
    assert content_hash(source.hash_input(sympla_html("evento_agatha_preco110.html"))) != base


def test_hash_sem_secao_de_ingressos_usa_texto_vazio():
    raw = source.hash_input("<html><body>sem ingressos</body></html>")
    assert raw["pricing"] == ""
    assert len(content_hash(raw)) == 64


@pytest.mark.parametrize(
    "texto",
    [
        "evento-teste-565",
        "blu-mp-juninho-teste-copia",
        "Show TESTE de som",
        "Teste",
        "Évento Tésté 2",
    ],
)
def test_eventos_de_teste(texto):
    assert is_test_event(texto)


@pytest.mark.parametrize(
    "texto", ["Testemunho de fé", "contestes", "campeonato-teste1", "", None, "Show de rock"]
)
def test_eventos_que_nao_sao_de_teste(texto):
    assert not is_test_event(texto)
