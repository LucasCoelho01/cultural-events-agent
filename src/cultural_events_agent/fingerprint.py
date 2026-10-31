"""Hash dos dados brutos da fonte e filtro de eventos de teste.

O hash é calculado antes de qualquer chamada ao modelo: item inalterado não gasta chamada.
"""

import hashlib
import json
import re
import unicodedata
from collections.abc import Mapping
from html import unescape
from typing import Any

# Versão da regra de normalização: mudá-la reextrai todos os itens uma vez.
HASH_VERSION = 1
TEST_WORD = "teste"
_WORDS = re.compile(r"[^a-z0-9]+")


def _normalize(value: Any) -> Any:
    if isinstance(value, str):
        return " ".join(unicodedata.normalize("NFC", unescape(value)).split())
    if isinstance(value, Mapping):
        return {str(key): _normalize(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_normalize(item) for item in value]
    return value


def content_hash(raw: Mapping[str, Any]) -> str:
    """SHA-256 (hex, 64 caracteres) do JSON canônico dos dados brutos normalizados."""
    payload = {"v": HASH_VERSION, **_normalize(raw)}
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def is_test_event(text: str | None) -> bool:
    """True se `text` (título ou slug) tem a palavra inteira `teste`, sem distinguir maiúsculas."""
    if not text:
        return False
    folded = unicodedata.normalize("NFKD", text.lower())
    plain = "".join(ch for ch in folded if not unicodedata.combining(ch))
    return TEST_WORD in _WORDS.split(plain)
