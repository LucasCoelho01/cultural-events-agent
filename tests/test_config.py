from pathlib import Path

import pytest

from cultural_events_agent.config import Settings, load_settings
from cultural_events_agent.errors import ConfigInvalid

ROOT = Path(__file__).resolve().parent.parent
API_KEY = "sk-ant-segredo-1234567890"
DB_PASSWORD = "s3nh4-do-banco"
DB_URL = f"postgresql://agent_writer:{DB_PASSWORD}@localhost:5432/cultural_events"
ENV_VARS = [
    "DATABASE_URL",
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_MODEL",
    "USER_AGENT",
    "REQUEST_DELAY_SECONDS",
]


@pytest.fixture(autouse=True)
def ambiente_limpo(monkeypatch):
    for name in ENV_VARS:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def env_minimo(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", DB_URL)
    monkeypatch.setenv("ANTHROPIC_API_KEY", API_KEY)
    monkeypatch.setenv("USER_AGENT", "cultural-events-agent/0.1 (contato@example.com)")


def test_padroes(env_minimo):
    settings = load_settings(env_file=None)
    assert settings.anthropic_model == "claude-sonnet-5-5"
    assert settings.request_delay_seconds == 2


def test_valores_sobrescritos(env_minimo, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_MODEL", "claude-haiku-5-5")
    monkeypatch.setenv("REQUEST_DELAY_SECONDS", "0.5")
    settings = load_settings(env_file=None)
    assert settings.anthropic_model == "claude-haiku-5-5"
    assert settings.request_delay_seconds == 0.5


def test_le_do_arquivo_env(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        f"DATABASE_URL={DB_URL}\nANTHROPIC_API_KEY={API_KEY}\nUSER_AGENT=agente\n"
        "MIGRATIONS_DIR=../outro\n",
        encoding="utf-8",
    )
    assert load_settings(env_file=env_file).user_agent == "agente"


@pytest.mark.parametrize("variavel", ["DATABASE_URL", "ANTHROPIC_API_KEY", "USER_AGENT"])
def test_variavel_obrigatoria_ausente(env_minimo, monkeypatch, variavel):
    monkeypatch.delenv(variavel)
    with pytest.raises(ConfigInvalid) as info:
        load_settings(env_file=None)
    assert info.value.code == "CONFIG_INVALID"
    assert variavel in info.value.message


@pytest.mark.parametrize("valor", ["abc", "-1", "nan", "inf"])
def test_atraso_invalido(env_minimo, monkeypatch, valor):
    monkeypatch.setenv("REQUEST_DELAY_SECONDS", valor)
    with pytest.raises(ConfigInvalid) as info:
        load_settings(env_file=None)
    assert "REQUEST_DELAY_SECONDS" in info.value.message


@pytest.mark.parametrize(
    "url",
    ["mysql://u:p@host/db", "postgres://u:p@host/db", "localhost:5432/db", "postgresql://"],
)
def test_url_do_banco_invalida(env_minimo, monkeypatch, url):
    monkeypatch.setenv("DATABASE_URL", url)
    with pytest.raises(ConfigInvalid) as info:
        load_settings(env_file=None)
    assert "DATABASE_URL" in info.value.message


@pytest.mark.parametrize("variavel", ["USER_AGENT", "ANTHROPIC_API_KEY", "ANTHROPIC_MODEL"])
def test_texto_vazio(env_minimo, monkeypatch, variavel):
    monkeypatch.setenv(variavel, "")
    with pytest.raises(ConfigInvalid) as info:
        load_settings(env_file=None)
    assert variavel in info.value.message


def test_erro_nao_vaza_senha_da_url_invalida(monkeypatch, env_minimo):
    monkeypatch.setenv("DATABASE_URL", f"mysql://u:{DB_PASSWORD}@host/db")
    with pytest.raises(ConfigInvalid) as info:
        load_settings(env_file=None)
    assert "DATABASE_URL" in info.value.message
    assert DB_PASSWORD not in str(info.value)


def test_erro_nao_vaza_chave_de_api(env_minimo, monkeypatch):
    monkeypatch.setenv("REQUEST_DELAY_SECONDS", "abc")
    with pytest.raises(ConfigInvalid) as info:
        load_settings(env_file=None)
    assert API_KEY not in str(info.value)
    assert DB_PASSWORD not in str(info.value)


def test_configuracao_impressa_mascara_segredos(env_minimo):
    settings = load_settings(env_file=None)
    for text in (repr(settings), str(settings), f"{settings}", settings.database_url_masked):
        assert API_KEY not in text
        assert DB_PASSWORD not in text
    assert "***" in repr(settings)
    assert (
        settings.database_url_masked
        == "postgresql://agent_writer:***@localhost:5432/cultural_events"
    )


def test_secret_values_inclui_chave_e_senha(env_minimo):
    secrets = load_settings(env_file=None).secret_values()
    assert API_KEY in secrets
    assert DB_PASSWORD in secrets


def test_env_example_lista_todas_as_variaveis():
    example = (ROOT / ".env.example").read_text(encoding="utf-8")
    declared = {
        line.split("=", 1)[0].strip()
        for line in example.splitlines()
        if "=" in line and not line.lstrip().startswith("#")
    }
    for field in Settings.model_fields:
        assert field.upper() in declared
    assert "MIGRATIONS_DIR" in declared
    assert "sk-ant-api" not in example


def test_env_fica_fora_do_controle_de_versao():
    lines = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert ".env" in lines
    assert "!.env.example" in lines


def test_segredos_fracos_sao_listados_sem_valores(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:curta@localhost/db")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "abc")
    monkeypatch.setenv("USER_AGENT", "teste")
    assert load_settings(env_file=None).weak_secret_names() == ["ANTHROPIC_API_KEY", "DATABASE_URL"]


def test_segredos_fortes_nao_geram_aviso(env_minimo):
    assert load_settings(env_file=None).weak_secret_names() == []


def test_url_sem_senha_nao_gera_aviso(env_minimo, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://localhost/db")
    assert load_settings(env_file=None).weak_secret_names() == []
