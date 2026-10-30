import anthropic
import httpx
import pytest

from cultural_events_agent.config import Settings
from cultural_events_agent.errors import LlmUnavailable
from cultural_events_agent.llm import AnthropicLlm, FakeLlm, LlmClient, build_llm

API_KEY = "sk-ant-chave-secreta-1234"
REQUEST = httpx.Request("POST", "https://api.anthropic.com/v1/messages")


def make_settings(**extra) -> Settings:
    return Settings(
        database_url="postgresql://u:p@localhost/db",
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
