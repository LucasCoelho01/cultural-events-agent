"""Interface das fontes: o grafo depende só disto, nunca de uma fonte concreta."""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field


@dataclass(frozen=True)
class DiscoveredItem:
    """Um evento achado na listagem: `external_id` identifica o item na fonte."""

    external_id: str
    url: str
    slug: str = ""


class Session(BaseModel):
    starts_at: datetime
    ends_at: datetime | None = None


class StructuredEvent(BaseModel):
    """O que a página entrega por dados estruturados; o que faltar o modelo completa."""

    title: str | None = None
    description: str | None = None
    sessions: list[Session] = Field(default_factory=list)
    image_url: str | None = None
    ticket_url: str | None = None
    venue_name: str | None = None
    address_line: str | None = None
    neighborhood: str | None = None
    postal_code: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    source_category: str | None = None
    price_min_cents: int | None = None
    price_max_cents: int | None = None
    is_free: bool | None = None
    age_rating: str | None = None
    is_cultural: bool | None = None  # veredito da própria fonte, se ela der um
    cultural_reason: str | None = None
    public: bool = True  # False: cancelado, não publicado ou privado na fonte


class Source(Protocol):
    code: str

    def listing_url(self, state_uf: str, city_slug: str, page: int = 1) -> str: ...

    def parse_listing(self, html: str) -> list[DiscoveredItem]: ...

    def structured_data(self, html: str, timezone: ZoneInfo) -> StructuredEvent: ...

    def pricing_text(self, html: str) -> str:
        """Texto da seção de ingressos/preços (vazio se a página não tiver a seção)."""
        ...

    def hash_input(self, html: str) -> Mapping[str, Any]:
        """Dados brutos relevantes, sem campos voláteis: chaves `next`, `ld` e `pricing`."""
        ...

    def page_text(self, html: str) -> str:
        """Texto visível da página, para o modelo."""
        ...
