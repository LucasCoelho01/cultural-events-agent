"""Sympla: listagem no servidor; evento com JSON-LD, __NEXT_DATA__ e ingressos no DOM."""

import json
import re
from collections.abc import Mapping
from datetime import datetime
from html import unescape
from typing import Any
from urllib.parse import urljoin
from zoneinfo import ZoneInfo

from cultural_events_agent.sources.base import DiscoveredItem, Session, StructuredEvent
from cultural_events_agent.sources.htmlparts import PageParts, parse_page

BASE_URL = "https://www.sympla.com.br"
TICKETS_TESTID = "ticket-grid"
_LISTING_LINK = re.compile(
    r"(?:https://www\.sympla\.com\.br)?/evento/(?P<slug>[a-z0-9][a-z0-9-]*)/(?P<id>\d+)"
)

# Campos de eventHydration.event que entram no hash; o resto é volátil ou de rastreio.
_NEXT_FIELDS = (
    "id", "name", "startDate", "endDate", "strippedDetail", "cancelled", "published", "visible",
    "privateEvent", "eventType", "paymentEventType",
)  # fmt: skip
_ADDRESS_FIELDS = (
    "name", "address", "addressNum", "addressAlt", "neighborhood", "city", "state", "zipCode",
    "geolocation",
)  # fmt: skip
_TRUNCATED = ("...", "…")


def _scripts(parts: PageParts) -> tuple[dict[str, Any], dict[str, Any]]:
    """(event do __NEXT_DATA__, JSON-LD Event); dicionário vazio se ausente ou ilegível."""
    next_event: dict[str, Any] = {}
    ld_event: dict[str, Any] = {}
    for attrs, text in parts.scripts:
        try:
            if attrs.get("id") == "__NEXT_DATA__":
                data = json.loads(text)
                next_event = data["props"]["pageProps"]["hydrationData"]["eventHydration"]["event"]
            elif attrs.get("type") == "application/ld+json" and not ld_event:
                data = json.loads(text)
                for item in data if isinstance(data, list) else [data]:
                    if isinstance(item, dict) and "Event" in str(item.get("@type")):
                        ld_event = item
                        break
        except (ValueError, KeyError, TypeError):
            continue
    return next_event, ld_event


def _parse_dt(value: Any, timezone: ZoneInfo) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip())
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone)


def _text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = " ".join(unescape(value).split())
    return cleaned or None


def _description(next_event: dict[str, Any], ld_event: dict[str, Any]) -> str | None:
    full = unescape(str(next_event.get("strippedDetail") or ""))
    lines = [" ".join(line.split()) for line in full.splitlines()]
    text = "\n".join(line for line in lines if line)
    if text:
        return text
    ld = _text(ld_event.get("description"))
    return None if ld is None or ld.endswith(_TRUNCATED) else ld


class SymplaSource:
    code = "sympla"

    def listing_url(self, state_uf: str, city_slug: str, page: int = 1) -> str:
        url = f"{BASE_URL}/eventos/{city_slug}-{state_uf.lower()}"
        return url if page <= 1 else f"{url}?page={page}"

    def parse_listing(self, html: str) -> list[DiscoveredItem]:
        """Eventos da listagem, na ordem em que aparecem, sem repetir o id.

        A listagem servida pelo servidor traz os eventos num JSON dentro de <script> (sem <a>);
        o DOM renderizado traz <a>. O padrão /evento/<slug>/<id> cobre os dois formatos.
        """
        found: dict[str, DiscoveredItem] = {}
        for match in _LISTING_LINK.finditer(html):
            if match["id"] not in found:
                found[match["id"]] = DiscoveredItem(
                    external_id=match["id"],
                    url=f"{BASE_URL}/evento/{match['slug']}/{match['id']}",
                    slug=match["slug"],
                )
        return list(found.values())

    def structured_data(self, html: str, timezone: ZoneInfo) -> StructuredEvent:
        next_event, ld_event = _scripts(parse_page(html, TICKETS_TESTID))
        address = next_event.get("eventsAddress") or {}
        place = ld_event.get("location") or {}
        ld_address = place.get("address") or {}
        geo = address.get("geolocation") or {}
        multi_start = (next_event.get("startDateMultiFormat") or {}).get("ISO8601")
        multi_end = (next_event.get("endDateMultiFormat") or {}).get("ISO8601")
        start = (
            _parse_dt(ld_event.get("startDate"), timezone)
            or _parse_dt(multi_start, timezone)
            or _parse_dt(next_event.get("startDate"), timezone)
        )
        end = (
            _parse_dt(ld_event.get("endDate"), timezone)
            or _parse_dt(multi_end, timezone)
            or _parse_dt(next_event.get("endDate"), timezone)
        )
        street = " ".join(
            str(address.get(key) or "").strip() for key in ("address", "addressNum", "addressAlt")
        ).strip()
        images = next_event.get("images") or {}
        ld_images = ld_event.get("image")
        ld_image = ld_images[0] if isinstance(ld_images, list) and ld_images else ld_images
        status = str(ld_event.get("eventStatus") or "")
        public = (
            not next_event.get("cancelled", False)
            and next_event.get("published", True)
            and next_event.get("visible", True)
            and not next_event.get("privateEvent", False)
            and "Cancelled" not in status
        )
        new_url = next_event.get("newUrl")
        return StructuredEvent(
            title=_text(next_event.get("name")) or _text(ld_event.get("name")),
            description=_description(next_event, ld_event),
            sessions=[Session(starts_at=start, ends_at=end)] if start else [],
            image_url=images.get("logoLarge") or images.get("logoUrl") or ld_image or None,
            ticket_url=urljoin(BASE_URL, new_url).split("?")[0] if new_url else None,
            venue_name=_text(address.get("name")) or _text(place.get("name")),
            address_line=street or _text(ld_address.get("streetAddress")),
            neighborhood=_text(address.get("neighborhood")),
            postal_code=_text(address.get("zipCode")) or _text(ld_address.get("postalCode")),
            latitude=geo.get("latitude"),
            longitude=geo.get("longitude"),
            source_category=_text((next_event.get("eventsCategory") or {}).get("slug")),
            public=bool(public),
        )

    def pricing_text(self, html: str) -> str:
        return parse_page(html, TICKETS_TESTID).section_text

    def hash_input(self, html: str) -> Mapping[str, Any]:
        parts = parse_page(html, TICKETS_TESTID)
        next_event, ld_event = _scripts(parts)
        selected: dict[str, Any] = {k: next_event[k] for k in _NEXT_FIELDS if k in next_event}
        address = next_event.get("eventsAddress") or {}
        selected["eventsAddress"] = {k: address[k] for k in _ADDRESS_FIELDS if k in address}
        selected["eventsCategory"] = (next_event.get("eventsCategory") or {}).get("slug")
        selected["logoUrl"] = (next_event.get("images") or {}).get("logoUrl")
        return {
            "next": selected if next_event else {},
            "ld": ld_event,
            "pricing": parts.section_text,
        }

    def page_text(self, html: str) -> str:
        return parse_page(html, TICKETS_TESTID).body_text
