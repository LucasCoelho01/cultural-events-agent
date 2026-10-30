import pytest

from cultural_events_agent.errors import (
    ConfigInvalid,
    DbContractViolation,
    DbUnavailable,
    LlmUnavailable,
    SchemaContractMismatch,
)


@pytest.mark.parametrize(
    ("error", "code", "exit_code"),
    [
        (ConfigInvalid, "CONFIG_INVALID", 2),
        (DbUnavailable, "DB_UNAVAILABLE", 3),
        (SchemaContractMismatch, "SCHEMA_CONTRACT_MISMATCH", 4),
        (DbContractViolation, "DB_CONTRACT_VIOLATION", 1),
        (LlmUnavailable, "LLM_UNAVAILABLE", 5),
    ],
)
def test_codigos_de_erro_e_de_saida(error, code, exit_code):
    exc = error("falhou")
    assert exc.code == code
    assert exc.exit_code == exit_code
    assert str(exc) == f"{code}: falhou"
