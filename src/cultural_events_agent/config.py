from pathlib import Path
from urllib.parse import quote, unquote, urlsplit

from pydantic import Field, SecretStr, ValidationError, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from cultural_events_agent.errors import ConfigInvalid
from cultural_events_agent.logs import MIN_SECRET_LENGTH

DEFAULT_MODEL = "claude-sonnet-5-5"
MASK = "***"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        hide_input_in_errors=True,
    )

    database_url: SecretStr
    anthropic_api_key: SecretStr = Field(min_length=1)
    anthropic_model: str = Field(default=DEFAULT_MODEL, min_length=1)
    user_agent: str = Field(min_length=1)
    request_delay_seconds: float = Field(default=2, ge=0, allow_inf_nan=False)

    @field_validator("database_url")
    @classmethod
    def _database_url_postgres(cls, value: SecretStr) -> SecretStr:
        # As mensagens daqui não podem incluir o valor: ele pode conter a senha.
        raw = value.get_secret_value()
        try:
            parts = urlsplit(raw)
            valid = parts.scheme == "postgresql" and bool(parts.hostname)
        except ValueError:
            valid = False
        if not valid:
            raise ValueError("deve ser uma URL postgresql://usuario:senha@host:porta/banco")
        return value

    @property
    def database_url_masked(self) -> str:
        """URL do banco com a senha trocada por `***`, segura para logs e mensagens."""
        parts = urlsplit(self.database_url.get_secret_value())
        if parts.password is None:
            return parts.geturl()
        user = quote(unquote(parts.username or ""), safe="")
        host = parts.hostname or ""
        port = f":{parts.port}" if parts.port else ""
        return parts._replace(netloc=f"{user}:{MASK}@{host}{port}").geturl()

    def secret_values(self) -> list[str]:
        """Valores que nunca podem aparecer em logs ou mensagens de erro."""
        secrets = [self.anthropic_api_key.get_secret_value()]
        password = urlsplit(self.database_url.get_secret_value()).password
        if password:
            secrets.extend([password, unquote(password)])
        return secrets

    def weak_secret_names(self) -> list[str]:
        """Variáveis cujo segredo é curto demais para ser mascarado nos logs (sem os valores)."""
        weak = []
        if len(self.anthropic_api_key.get_secret_value()) < MIN_SECRET_LENGTH:
            weak.append("ANTHROPIC_API_KEY")
        password = urlsplit(self.database_url.get_secret_value()).password
        if password and len(unquote(password)) < MIN_SECRET_LENGTH:
            weak.append("DATABASE_URL")
        return weak

    def __repr__(self) -> str:
        return (
            f"Settings(database_url={self.database_url_masked!r}, anthropic_api_key={MASK!r}, "
            f"anthropic_model={self.anthropic_model!r}, user_agent={self.user_agent!r}, "
            f"request_delay_seconds={self.request_delay_seconds!r})"
        )

    __str__ = __repr__


def load_settings(env_file: str | Path | None = ".env") -> Settings:
    """Carrega a configuração do ambiente e do `.env`.

    Falhas viram `ConfigInvalid` com só o nome da variável e o motivo, nunca o valor recebido.
    """
    try:
        return Settings(_env_file=env_file)
    except ValidationError as exc:
        problems = [
            f"{'.'.join(str(part) for part in err['loc']).upper()}: {err['msg']}"
            for err in exc.errors(include_input=False, include_url=False)
        ]
        raise ConfigInvalid("; ".join(problems)) from None
