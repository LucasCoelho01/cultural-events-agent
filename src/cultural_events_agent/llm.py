"""Acesso ao modelo de linguagem por uma interface, sem acoplar o código ao provedor."""

from typing import Any, Protocol

import anthropic
from langchain_anthropic import ChatAnthropic

from cultural_events_agent.config import Settings
from cultural_events_agent.errors import LlmUnavailable

PING_MAX_TOKENS = 64


class LlmClient(Protocol):
    """O que o agente precisa do modelo: um prompt de texto e uma resposta de texto."""

    def invoke(self, prompt: str) -> str: ...


class AnthropicLlm:
    """Claude via langchain-anthropic; falhas do provedor viram LLM_UNAVAILABLE."""

    def __init__(self, settings: Settings, chat: Any | None = None) -> None:
        self._secrets = settings.secret_values()
        self.chat = chat or ChatAnthropic(
            model=settings.anthropic_model,
            api_key=settings.anthropic_api_key,
            max_tokens=1024,
        )

    @property
    def model_name(self) -> str:
        return self.chat.model

    def invoke(self, prompt: str) -> str:
        try:
            response = self.chat.invoke(prompt)
        except anthropic.AnthropicError as exc:
            raise LlmUnavailable(f"{type(exc).__name__}: {self._scrub(str(exc))}") from None
        return str(response.text)

    def _scrub(self, text: str) -> str:
        for secret in self._secrets:
            text = text.replace(secret, "***")
        return text


class FakeLlm:
    """Modelo falso para testes: devolve respostas fixas, sem rede e sem chave de API."""

    def __init__(self, response: str = "pong", error: Exception | None = None) -> None:
        self.response = response
        self.error = error
        self.prompts: list[str] = []

    def invoke(self, prompt: str) -> str:
        self.prompts.append(prompt)
        if self.error is not None:
            raise self.error
        return self.response


def build_llm(settings: Settings) -> LlmClient:
    """Fábrica padrão: Claude com o modelo de ANTHROPIC_MODEL."""
    return AnthropicLlm(settings)
