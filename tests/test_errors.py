import pytest

from cultural_events_agent.errors import (
    CityInactive,
    CityNotFound,
    ConfigInvalid,
    DbContractViolation,
    DbUnavailable,
    ExtractionInvalid,
    FetchFailed,
    ItemFailed,
    LlmUnavailable,
    RateLimited,
    RobotsDisallowed,
    SchemaContractMismatch,
    SourceNotFound,
    VersionConflict,
)


@pytest.mark.parametrize(
    ("error", "code", "exit_code"),
    [
        (ConfigInvalid, "CONFIG_INVALID", 2),
        (DbUnavailable, "DB_UNAVAILABLE", 3),
        (SchemaContractMismatch, "SCHEMA_CONTRACT_MISMATCH", 4),
        (DbContractViolation, "DB_CONTRACT_VIOLATION", 1),
        (LlmUnavailable, "LLM_UNAVAILABLE", 5),
        (CityNotFound, "CITY_NOT_FOUND", 6),
        (CityInactive, "CITY_INACTIVE", 7),
        (SourceNotFound, "SOURCE_NOT_FOUND", 8),
        (RobotsDisallowed, "ROBOTS_DISALLOWED", 9),
        (FetchFailed, "FETCH_FAILED", 10),
        (ExtractionInvalid, "EXTRACTION_INVALID", 1),
        (VersionConflict, "VERSION_CONFLICT", 1),
        (ItemFailed, "ITEM_FAILED", 1),
        (RateLimited, "RATE_LIMITED", 11),
    ],
)
def test_codigos_de_erro_e_de_saida(error, code, exit_code):
    exc = error("falhou")
    assert exc.code == code
    assert exc.exit_code == exit_code
    assert str(exc) == f"{code}: falhou"


def test_describe_validation_error_sem_o_input():
    from pydantic import BaseModel, ValidationError

    from cultural_events_agent.errors import describe_validation_error

    class M(BaseModel):
        a: int
        b: list[int]

    with pytest.raises(ValidationError) as exc:
        M.model_validate({"a": "valor-sigiloso", "b": [1, "x"]})
    texto = describe_validation_error(exc.value)
    assert "a [int_parsing]" in texto and "b.1 [int_parsing]" in texto
    assert "valor-sigiloso" not in texto and "unable to parse string as an integer" in texto


def test_describe_validation_error_de_excecao_comum_so_traz_o_tipo():
    from cultural_events_agent.errors import describe_validation_error

    assert describe_validation_error(KeyError("x")) == "KeyError"
