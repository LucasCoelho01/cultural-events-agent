"""Acesso ao modelo de linguagem por uma interface, sem acoplar o código ao provedor."""

from collections.abc import Callable
from typing import Any, Protocol, TypeVar

import anthropic
from langchain_anthropic import ChatAnthropic
from langchain_core.exceptions import OutputParserException
from pydantic import BaseModel, ValidationError

from cultural_events_agent.config import Settings
from cultural_events_agent.errors import (
    ExtractionInvalid,
    LlmUnavailable,
    describe_validation_error,
)

PING_MAX_TOKENS = 64
EXTRACT_MAX_TOKENS = 4096
MAX_INVALID_DETAIL = 1500

T = TypeVar("T", bound=BaseModel)


class LlmClient(Protocol):
    """O que o agente precisa do modelo: um prompt de texto e uma resposta de texto."""

    last_raw: Any  # resposta bruta da última extração (None se não houve), para depuração

    def invoke(self, prompt: str) -> str: ...

    def extract(self, prompt: str, schema: type[T]) -> T:
        """Saída estruturada validada em `schema`; resposta inválida vira EXTRACTION_INVALID."""
        ...


class AnthropicLlm:
    """Claude via langchain-anthropic; falhas do provedor viram LLM_UNAVAILABLE."""

    def __init__(self, settings: Settings, chat: Any | None = None) -> None:
        self._secrets = settings.secret_values()
        self.last_raw: Any = None
        self.chat = chat or ChatAnthropic(
            model=settings.anthropic_model,
            api_key=settings.anthropic_api_key,
            max_tokens=EXTRACT_MAX_TOKENS,
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

    def extract(self, prompt: str, schema: type[T]) -> T:
        self.last_raw = None
        try:
            runnable = self.chat.with_structured_output(
                schema, method="json_schema", include_raw=True
            )
            result = runnable.invoke(prompt)
        except anthropic.AnthropicError as exc:
            raise LlmUnavailable(f"{type(exc).__name__}: {self._scrub(str(exc))}") from None
        except (OutputParserException, ValueError) as exc:  # ValidationError é um ValueError
            raise ExtractionInvalid(_invalid_message(exc, self._scrub)) from None
        if isinstance(result, dict) and {"raw", "parsed", "parsing_error"} <= set(result):
            self.last_raw = _raw_payload(result["raw"])
            if result["parsing_error"] is not None:
                raise ExtractionInvalid(_invalid_message(result["parsing_error"], self._scrub))
            result = result["parsed"]
        if isinstance(result, dict):
            try:
                result = schema.model_validate(result)
            except ValidationError as exc:
                raise ExtractionInvalid(_invalid_message(exc, self._scrub)) from None
        if not isinstance(result, schema):
            raise ExtractionInvalid("o modelo não devolveu a saída estruturada pedida")
        return result

    def _scrub(self, text: str) -> str:
        for secret in self._secrets:
            text = text.replace(secret, "***")
        return text


class FakeLlm:
    """Modelo falso para testes: devolve respostas fixas, sem rede e sem chave de API."""

    def __init__(
        self,
        response: str = "pong",
        error: Exception | None = None,
        structured: Any = None,
    ) -> None:
        """`structured` é uma instância, um dicionário ou uma lista delas, usadas em ordem."""
        self.response = response
        self.error = error
        self._structured = list(structured) if isinstance(structured, list) else [structured]
        self.prompts: list[str] = []
        self.last_raw: Any = None

    def invoke(self, prompt: str) -> str:
        self.prompts.append(prompt)
        if self.error is not None:
            raise self.error
        return self.response

    def extract(self, prompt: str, schema: type[T]) -> T:
        self.prompts.append(prompt)
        if self.error is not None:
            raise self.error
        if not self._structured or self._structured[0] is None:
            raise AssertionError("FakeLlm sem resposta estruturada configurada")
        item = self._structured.pop(0) if len(self._structured) > 1 else self._structured[0]
        data = item.model_dump() if isinstance(item, BaseModel) else item
        self.last_raw = data
        try:
            return schema.model_validate(data)
        except ValidationError as exc:
            raise ExtractionInvalid(_invalid_message(exc)) from None


def _invalid_message(exc: Exception, scrub: Callable[[str], str] = lambda text: text) -> str:
    """Mensagem de EXTRACTION_INVALID: tipo e mensagem da exceção, com os segredos mascarados.

    Para ValidationError, os campos (`loc [type] msg`, sem o valor recebido) no lugar de `str(exc)`.
    """
    if isinstance(exc, ValidationError):
        detail = f"ValidationError: {describe_validation_error(exc)}"
    else:
        detail = f"{type(exc).__name__}: {str(exc).strip()}"
    return scrub(f"a saída do modelo não validou no esquema pedido: {detail}")[:MAX_INVALID_DETAIL]


def _raw_payload(raw: Any) -> Any:
    """A mensagem bruta do modelo em formato serializável (para o .debug/)."""
    try:
        return raw.model_dump(mode="json")
    except Exception:
        return str(raw)


def build_llm(settings: Settings) -> LlmClient:
    """Fábrica padrão: Claude com o modelo de ANTHROPIC_MODEL."""
    return AnthropicLlm(settings)
