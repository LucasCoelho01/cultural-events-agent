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
