"""Logs estruturados: um objeto JSON por linha no stderr, com segredos mascarados."""

import json
import logging
import sys
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any

LOGGER_NAME = "cultural_events_agent"
MASK = "***"
# Segredos menores que isso não são mascarados: trocar "abc" por *** estragaria texto comum.
# A config avisa (CONFIG_WEAK_SECRET) em vez disso.
MIN_SECRET_LENGTH = 8

# Atributos que todo LogRecord já tem; o que sobra veio de `extra=` e vai para o JSON.
_RESERVED = set(vars(logging.makeLogRecord({}))) | {"message", "asctime", "taskName"}


class JsonFormatter(logging.Formatter):
    def __init__(self, secrets: Iterable[str] = ()) -> None:
        super().__init__()
        self._secrets = [s for s in secrets if len(s) >= MIN_SECRET_LENGTH]

    def _scrub(self, value: Any) -> Any:
        if isinstance(value, str):
            for secret in self._secrets:
                value = value.replace(secret, MASK)
        return value

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "event": self._scrub(record.getMessage()),
        }
        for key, value in vars(record).items():
            if key not in _RESERVED:
                payload[key] = self._scrub(
                    value if isinstance(value, int | float | bool) else str(value)
                )
        return json.dumps(payload, ensure_ascii=True)


def configure_logging(secrets: Iterable[str] = (), level: int = logging.INFO) -> logging.Logger:
    """Liga o log JSON no stderr; `secrets` são valores que nunca podem sair nos logs."""
    logger = logging.getLogger(LOGGER_NAME)
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(JsonFormatter(secrets))
    logger.addHandler(handler)
    logger.setLevel(level)
    logger.propagate = False
    return logger
