import anthropic
import httpx
import pytest
from pydantic import BaseModel

from cultural_events_agent.config import Settings
from cultural_events_agent.errors import ExtractionInvalid, LlmUnavailable
from cultural_events_agent.llm import AnthropicLlm, FakeLlm, LlmClient, build_llm

API_KEY = "sk-ant-chave-secreta-1234"
REQUEST = httpx.Request("POST", "https://api.anthropic.com/v1/messages")


def make_settings(**extra) -> Settings:
    return Settings(
        database_url="postgresql://u:senha-do-banco-1234@localhost/db",
        anthropic_api_key=API_KEY,
        user_agent="teste",
        _env_file=None,
        **extra,
    )


class StubChat:
    """Substitui o ChatAnthropic: devolve um texto ou levanta o erro dado."""

    model = "stub"

    def __init__(self, error: Exception | None = None, text: str = "pong") -> None:
        self.error = error
        self.text = text

    def invoke(self, prompt):
        if self.error:
            raise self.error
        return type("Resposta", (), {"text": self.text})()


def test_modelo_padrao():
    assert build_llm(make_settings()).model_name == "claude-sonnet-5-5"


def test_modelo_configurado():
    llm = build_llm(make_settings(anthropic_model="claude-haiku-5-5"))
    assert llm.model_name == "claude-haiku-5-5"


def test_fabrica_devolve_a_interface():
    llm: LlmClient = build_llm(make_settings())
    assert isinstance(llm, AnthropicLlm)


def test_fake_llm_responde_sem_rede():
    fake = FakeLlm("resposta fixa")
    assert fake.invoke("oi") == "resposta fixa"
    assert fake.prompts == ["oi"]


def test_resposta_do_modelo_e_texto():
    assert AnthropicLlm(make_settings(), chat=StubChat(text="ok")).invoke("oi") == "ok"


def test_chave_recusada_vira_llm_unavailable():
    erro = anthropic.AuthenticationError(
        f"invalid x-api-key: {API_KEY}",
        response=httpx.Response(401, request=REQUEST),
        body=None,
    )
    llm = AnthropicLlm(make_settings(), chat=StubChat(error=erro))
    with pytest.raises(LlmUnavailable) as info:
        llm.invoke("oi")
    assert info.value.code == "LLM_UNAVAILABLE"
    assert "AuthenticationError" in info.value.message
    assert API_KEY not in str(info.value)


def test_falha_de_rede_vira_llm_unavailable():
    llm = AnthropicLlm(
        make_settings(), chat=StubChat(error=anthropic.APIConnectionError(request=REQUEST))
    )
    with pytest.raises(LlmUnavailable) as info:
        llm.invoke("oi")
    assert info.value.code == "LLM_UNAVAILABLE"


class Ficha(BaseModel):
    titulo: str
    preco_cents: int


class StubStructuredChat(StubChat):
    """with_structured_output devolve um executável que responde `result` ou levanta `error`."""

    def __init__(self, result=None, error: Exception | None = None) -> None:
        super().__init__()
        self.result = result
        self.structured_error = error
        self.schema = None
        self.method = None
        self.include_raw = None
        self.wrapped = None  # se definido, devolve {raw, parsed, parsing_error}

    def with_structured_output(self, schema, method=None, include_raw=False):
        self.schema = schema
        self.method = method
        self.include_raw = include_raw
        outer = self

        class Runnable:
            def invoke(self, prompt):
                if outer.structured_error:
                    raise outer.structured_error
                if outer.wrapped is not None:
                    return outer.wrapped
                return outer.result

        return Runnable()


def test_extract_devolve_a_instancia_validada():
    chat = StubStructuredChat(result=Ficha(titulo="Show", preco_cents=1000))
    ficha = AnthropicLlm(make_settings(), chat=chat).extract("extraia", Ficha)
    assert ficha == Ficha(titulo="Show", preco_cents=1000)
    assert chat.schema is Ficha
    assert chat.method == "json_schema"
    assert chat.include_raw is True


def test_extract_aceita_dicionario_valido():
    chat = StubStructuredChat(result={"titulo": "Show", "preco_cents": 5})
    assert AnthropicLlm(make_settings(), chat=chat).extract("x", Ficha).preco_cents == 5


@pytest.mark.parametrize("result", [None, {"titulo": "Show"}, "texto solto"])
def test_extract_saida_invalida_vira_extraction_invalid(result):
    chat = StubStructuredChat(result=result)
    with pytest.raises(ExtractionInvalid):
        AnthropicLlm(make_settings(), chat=chat).extract("x", Ficha)


def test_extract_erro_de_parser_vira_extraction_invalid():
    from langchain_core.exceptions import OutputParserException

    chat = StubStructuredChat(error=OutputParserException("lixo"))
    with pytest.raises(ExtractionInvalid):
        AnthropicLlm(make_settings(), chat=chat).extract("x", Ficha)


def test_extract_provedor_indisponivel_nao_expoe_a_chave():
    erro = anthropic.AuthenticationError(
        f"chave {API_KEY} recusada",
        response=httpx.Response(401, request=REQUEST),
        body=None,
    )
    chat = StubStructuredChat(error=erro)
    with pytest.raises(LlmUnavailable) as exc:
        AnthropicLlm(make_settings(), chat=chat).extract("x", Ficha)
    assert API_KEY not in str(exc.value)


def test_fake_extract_com_dicionario_valido_registra_o_prompt():
    fake = FakeLlm(structured={"titulo": "Show", "preco_cents": 1})
    assert fake.extract("prompt", Ficha) == Ficha(titulo="Show", preco_cents=1)
    assert fake.prompts == ["prompt"]


def test_fake_extract_com_dicionario_invalido():
    fake = FakeLlm(structured={"titulo": "Show"})
    with pytest.raises(ExtractionInvalid):
        fake.extract("prompt", Ficha)


def test_fake_extract_consome_a_lista_em_ordem():
    fake = FakeLlm(
        structured=[{"titulo": "A", "preco_cents": 1}, {"titulo": "B", "preco_cents": 2}]
    )
    assert fake.extract("p", Ficha).titulo == "A"
    assert fake.extract("p", Ficha).titulo == "B"
    assert fake.extract("p", Ficha).titulo == "B"


def test_fake_extract_sem_configuracao_falha_alto():
    with pytest.raises(AssertionError):
        FakeLlm().extract("p", Ficha)


def test_extract_value_error_do_parser_vira_extraction_invalid():
    chat = StubStructuredChat(error=ValueError("json quebrado"))
    with pytest.raises(ExtractionInvalid):
        AnthropicLlm(make_settings(), chat=chat).extract("x", Ficha)


def test_extract_validation_error_traz_os_campos():
    try:
        Ficha.model_validate({"titulo": "x"})
    except ValueError as erro:  # ValidationError é um ValueError
        chat = StubStructuredChat(error=erro)
    with pytest.raises(ExtractionInvalid):
        AnthropicLlm(make_settings(), chat=chat).extract("x", Ficha)


class Mensagem(BaseModel):
    content: str


def _erro_de_validacao():
    try:
        Ficha.model_validate({"titulo": 1, "preco_cents": "segredo-do-usuario"})
    except ValueError as erro:
        return erro


def test_extract_com_erro_de_parser_traz_loc_type_e_msg_sem_o_input():
    chat = StubStructuredChat()
    chat.wrapped = {
        "raw": Mensagem(content="resposta crua"),
        "parsed": None,
        "parsing_error": _erro_de_validacao(),
    }
    llm = AnthropicLlm(make_settings(), chat=chat)
    with pytest.raises(ExtractionInvalid) as exc:
        llm.extract("x", Ficha)
    detalhe = exc.value.message
    assert "titulo [string_type]" in detalhe and "preco_cents [int_parsing]" in detalhe
    assert "segredo-do-usuario" not in detalhe
    assert llm.last_raw == {"content": "resposta crua"}


def test_extract_com_include_raw_devolve_o_parseado_e_guarda_a_resposta_crua():
    chat = StubStructuredChat()
    chat.wrapped = {
        "raw": Mensagem(content="ok"),
        "parsed": Ficha(titulo="Show", preco_cents=1),
        "parsing_error": None,
    }
    llm = AnthropicLlm(make_settings(), chat=chat)
    assert llm.extract("x", Ficha).titulo == "Show"
    assert llm.last_raw == {"content": "ok"}


def test_extract_value_error_traz_a_mensagem_da_excecao():
    chat = StubStructuredChat(error=ValueError("`temperature` is not supported"))
    with pytest.raises(ExtractionInvalid) as exc:
        AnthropicLlm(make_settings(), chat=chat).extract("x", Ficha)
    assert "ValueError: `temperature` is not supported" in exc.value.message


def test_extract_mascara_segredos_na_mensagem_da_excecao():
    chat = StubStructuredChat(error=ValueError(f"falha com a chave {API_KEY}"))
    with pytest.raises(ExtractionInvalid) as exc:
        AnthropicLlm(make_settings(), chat=chat).extract("x", Ficha)
    assert API_KEY not in exc.value.message and "falha com a chave ***" in exc.value.message


def test_extract_output_parser_exception_traz_tipo_e_mensagem():
    from langchain_core.exceptions import OutputParserException

    chat = StubStructuredChat(error=OutputParserException("json invalido na posicao 3"))
    with pytest.raises(ExtractionInvalid) as exc:
        AnthropicLlm(make_settings(), chat=chat).extract("x", Ficha)
    assert "OutputParserException: json invalido na posicao 3" in exc.value.message


def test_chat_padrao_nao_envia_parametros_que_o_modelo_recusa():
    """O ChatAnthropic valida a configuração antes de chamar a API (ex.: temperature != padrão)."""
    from langchain_core.messages import HumanMessage

    llm = build_llm(make_settings())
    assert llm.chat.temperature is None
    llm.chat._get_request_payload([HumanMessage(content="oi")])  # não levanta ValueError


def test_fake_extract_invalido_traz_detalhes_e_guarda_a_resposta():
    fake = FakeLlm(structured={"titulo": "Show", "preco_cents": "caro"})
    with pytest.raises(ExtractionInvalid) as exc:
        fake.extract("p", Ficha)
    assert "preco_cents [int_parsing]" in exc.value.message and "caro" not in exc.value.message
    assert fake.last_raw == {"titulo": "Show", "preco_cents": "caro"}


def test_validation_error_traz_os_campos_e_nao_o_valor_recebido():
    chat = StubStructuredChat(error=_erro_de_validacao())
    with pytest.raises(ExtractionInvalid) as exc:
        AnthropicLlm(make_settings(), chat=chat).extract("x", Ficha)
    mensagem = exc.value.message
    assert "ValidationError:" in mensagem and "preco_cents [int_parsing]" in mensagem
    assert "segredo-do-usuario" not in mensagem
