class AgentError(Exception):
    """Erro conhecido do agente, com código explícito e código de saída do processo."""

    code: str = "UNEXPECTED_ERROR"
    exit_code: int = 1

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message

    def __str__(self) -> str:
        return f"{self.code}: {self.message}"


class ConfigInvalid(AgentError):
    code = "CONFIG_INVALID"
    exit_code = 2


class DbUnavailable(AgentError):
    code = "DB_UNAVAILABLE"
    exit_code = 3


class SchemaContractMismatch(AgentError):
    code = "SCHEMA_CONTRACT_MISMATCH"
    exit_code = 4


class DbContractViolation(AgentError):
    code = "DB_CONTRACT_VIOLATION"
    exit_code = 1


class LlmUnavailable(AgentError):
    code = "LLM_UNAVAILABLE"
    exit_code = 5


class CityNotFound(AgentError):
    code = "CITY_NOT_FOUND"
    exit_code = 6


class CityInactive(AgentError):
    code = "CITY_INACTIVE"
    exit_code = 7


class SourceNotFound(AgentError):
    code = "SOURCE_NOT_FOUND"
    exit_code = 8


class RobotsDisallowed(AgentError):
    """Na listagem aborta a execução (saída 9); num evento falha só o item."""

    code = "ROBOTS_DISALLOWED"
    exit_code = 9


class FetchFailed(AgentError):
    """Na listagem aborta a execução (saída 10); num evento falha só o item."""

    code = "FETCH_FAILED"
    exit_code = 10


class ExtractionInvalid(AgentError):
    """Só por item: a execução segue com os demais."""

    code = "EXTRACTION_INVALID"


class VersionConflict(AgentError):
    """Só por item: o evento mudou durante todas as tentativas de atualização."""

    code = "VERSION_CONFLICT"


class ItemFailed(AgentError):
    """Só por item: exceção inesperada ao processar um evento; a execução segue."""

    code = "ITEM_FAILED"


MAX_VALIDATION_DETAIL = 1000


def describe_validation_error(exc: Exception) -> str:
    """`loc [type] msg` de cada erro de validação do pydantic, sem o valor recebido (input)."""
    errors = getattr(exc, "errors", None)
    if not callable(errors):
        return type(exc).__name__
    parts = []
    for err in errors(include_input=False, include_url=False, include_context=False):
        loc = ".".join(str(part) for part in err["loc"]) or "item"
        parts.append(f"{loc} [{err['type']}] {err['msg']}")
    return "; ".join(parts)[:MAX_VALIDATION_DETAIL]


class RateLimited(FetchFailed):
    """HTTP 429 da fonte. Num item falha só o item; na listagem, ou com 429 seguidos, aborta."""

    code = "RATE_LIMITED"
    exit_code = 11

    def __init__(self, message: str, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after  # segundos pedidos pelo servidor em Retry-After
