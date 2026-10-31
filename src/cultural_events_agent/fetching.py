"""Coleta educada: robots.txt, USER_AGENT, pausa por domínio, timeout e limite de páginas.

O acesso à rede fica atrás de `PageFetcher`, que os testes substituem por páginas salvas.
"""

import logging
import time
from collections.abc import Callable
from typing import Protocol
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

import httpx

from cultural_events_agent.config import Settings
from cultural_events_agent.errors import FetchFailed, RateLimited, RobotsDisallowed
from cultural_events_agent.logs import LOGGER_NAME

log = logging.getLogger(LOGGER_NAME)

TICKETS_SELECTOR = '[data-testid="ticket-grid-item"]'

# Espera por domínio depois de um 429: Retry-After do servidor, ou 30 s dobrando a cada 429 seguido.
RATE_LIMIT_DEFAULT_WAIT = 30.0
RATE_LIMIT_MAX_WAIT = 120.0


def parse_retry_after(value: str | None) -> float | None:
    """Retry-After em segundos; data HTTP ou lixo viram None (vale a espera padrão)."""
    if value is None or not value.strip().isdigit():
        return None
    return float(value.strip())


class PageFetcher(Protocol):
    """O que o fluxo precisa para baixar páginas, já respeitando as regras de coleta."""

    @property
    def pages_remaining(self) -> int: ...

    def get_listing(self, url: str) -> str:
        """Página de listagem (renderizada no servidor, sem JavaScript)."""
        ...

    def get_event(self, url: str) -> str:
        """Página de evento já renderizada (com JavaScript)."""
        ...


class Renderer(Protocol):
    """Abre uma URL num navegador e devolve o HTML renderizado."""

    def render(self, url: str, timeout_seconds: float) -> str: ...

    def close(self) -> None: ...


class PlaywrightRenderer:
    """Chromium via Playwright; um único navegador é reaproveitado entre as páginas."""

    def __init__(self, user_agent: str) -> None:
        self._user_agent = user_agent
        self._playwright = None
        self._browser = None
        self._context = None

    def _ensure_started(self) -> None:
        if self._context is not None:
            return
        from playwright.sync_api import sync_playwright

        self._playwright = sync_playwright().start()
        self._browser = self._playwright.chromium.launch()
        self._context = self._browser.new_context(user_agent=self._user_agent, locale="pt-BR")

    def render(self, url: str, timeout_seconds: float) -> str:
        """HTML renderizado. Espera o DOM e o ingresso, não o `networkidle`: página de 429 ou com
        rastreadores nunca fica ociosa, e esperar por isso custava o timeout inteiro por página.
        Erro inesperado do navegador (ex.: TargetClosedError) reinicia o navegador.
        """
        self._ensure_started()
        page = None
        try:
            page = self._context.new_page()
            response = page.goto(url, wait_until="domcontentloaded", timeout=timeout_seconds * 1000)
            if response is None:
                raise FetchFailed(f"sem resposta ao abrir {url}")
            if response.status == 429:
                raise RateLimited(
                    f"HTTP 429 ao abrir {url}",
                    retry_after=parse_retry_after(response.headers.get("retry-after")),
                )
            if response.status >= 400:
                raise FetchFailed(f"HTTP {response.status} ao abrir {url}")
            self._wait_for_content(page, timeout_seconds)
            return page.content()
        except FetchFailed:
            raise
        except Exception:
            self._restart()
            raise
        finally:
            if page is not None:
                try:
                    page.close()
                except Exception:  # o navegador pode já ter caído
                    pass

    def _restart(self) -> None:
        """Descarta o navegador; o próximo `render` abre um novo."""
        self.close()

    @staticmethod
    def _wait_for_content(page, timeout_seconds: float) -> None:
        """Os ingressos chegam por XHR depois do DOM; sem eles o preço some."""
        from playwright.sync_api import TimeoutError as PlaywrightTimeout

        try:
            page.wait_for_selector(
                TICKETS_SELECTOR, state="attached", timeout=min(timeout_seconds, 10) * 1000
            )
        except PlaywrightTimeout:
            pass  # evento sem ingressos à venda: o aviso pricing_section_missing cobre o caso

    def close(self) -> None:
        """Cada recurso é fechado no seu try: uma falha não deixa o Playwright rodando."""
        resources = (
            (self._context, "close"),
            (self._browser, "close"),
            (self._playwright, "stop"),
        )
        self._playwright = self._browser = self._context = None
        for resource, method in resources:
            if resource is None:
                continue
            try:
                getattr(resource, method)()
            except Exception:  # o navegador pode já ter caído
                pass


class PoliteFetcher:
    """Aplica robots.txt, User-Agent, pausa por domínio, timeout e limite de páginas."""

    def __init__(
        self,
        settings: Settings,
        *,
        http: httpx.Client | None = None,
        renderer: Renderer | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._user_agent = settings.user_agent
        self._delay = settings.request_delay_seconds
        self._timeout = settings.request_timeout_seconds
        self._max_pages = settings.max_pages_per_run
        self._http = http or httpx.Client()
        self._renderer = renderer or PlaywrightRenderer(settings.user_agent)
        self._clock = clock
        self._sleep = sleep
        self._last_request: dict[str, float] = {}
        self._robots: dict[str, RobotFileParser] = {}
        self._cooldown_until: dict[str, float] = {}
        self._rate_limited_in_a_row: dict[str, int] = {}
        self.pages_fetched = 0

    @property
    def pages_remaining(self) -> int:
        return max(self._max_pages - self.pages_fetched, 0)

    def close(self) -> None:
        self._renderer.close()
        self._http.close()

    def get_listing(self, url: str) -> str:
        self._before_page(url)
        try:
            response = self._request(url)
        except httpx.HTTPError as exc:
            raise FetchFailed(f"{type(exc).__name__} ao baixar {url}") from None
        finally:
            self._mark(url)
        if response.status_code == 429:
            raise self._rate_limited(
                url,
                RateLimited(
                    f"HTTP 429 ao baixar {url}",
                    retry_after=parse_retry_after(response.headers.get("retry-after")),
                ),
            )
        if response.status_code >= 400:
            raise FetchFailed(f"HTTP {response.status_code} ao baixar {url}")
        self._calm(url)
        return response.text

    def get_event(self, url: str) -> str:
        self._before_page(url)
        try:
            html = self._renderer.render(url, self._timeout)
        except RateLimited as exc:
            raise self._rate_limited(url, exc) from None
        except FetchFailed:
            raise
        except Exception as exc:  # timeout ou erro do navegador
            raise FetchFailed(f"{type(exc).__name__} ao renderizar {url}") from None
        finally:
            self._mark(url)
        self._calm(url)
        return html

    def _rate_limited(self, url: str, exc: RateLimited) -> RateLimited:
        """Registra o 429: o domínio espera antes da próxima requisição (sem nova tentativa)."""
        domain = self._domain(url)
        count = self._rate_limited_in_a_row.get(domain, 0) + 1
        self._rate_limited_in_a_row[domain] = count
        wait = exc.retry_after
        if wait is None:
            wait = RATE_LIMIT_DEFAULT_WAIT * 2 ** (count - 1)
        wait = min(wait, RATE_LIMIT_MAX_WAIT)
        self._cooldown_until[domain] = self._clock() + wait
        log.warning(
            "rate_limited", extra={"domain": domain, "wait_seconds": wait, "consecutive": count}
        )
        return exc

    def _calm(self, url: str) -> None:
        self._rate_limited_in_a_row[self._domain(url)] = 0

    def _before_page(self, url: str) -> None:
        self._check_robots(url)
        if self.pages_remaining <= 0:
            raise FetchFailed(f"limite de {self._max_pages} páginas por execução atingido")
        self.pages_fetched += 1
        self._wait(url)

    def _request(self, url: str) -> httpx.Response:
        return self._http.get(
            url,
            headers={"User-Agent": self._user_agent, "Accept-Language": "pt-BR"},
            timeout=self._timeout,
            follow_redirects=True,
        )

    def _domain(self, url: str) -> str:
        parts = urlsplit(url)
        return f"{parts.scheme}://{parts.netloc}"

    def _wait(self, url: str) -> None:
        domain = self._domain(url)
        now = self._clock()
        waits = [0.0]
        last = self._last_request.get(domain)
        if last is not None:
            waits.append(self._delay - (now - last))
        cooldown = self._cooldown_until.get(domain)
        if cooldown is not None:
            waits.append(cooldown - now)
        remaining = max(waits)
        if remaining > 0:
            self._sleep(remaining)

    def _mark(self, url: str) -> None:
        self._last_request[self._domain(url)] = self._clock()

    def _check_robots(self, url: str) -> None:
        if not self._robots_for(url).can_fetch(self._user_agent, url):
            raise RobotsDisallowed(f"o robots.txt não permite baixar {url}")

    def _robots_for(self, url: str) -> RobotFileParser:
        domain = self._domain(url)
        cached = self._robots.get(domain)
        if cached is not None:
            return cached
        robots_url = f"{domain}/robots.txt"
        parser = RobotFileParser()
        self._wait(robots_url)
        try:
            response = self._request(robots_url)
        except httpx.HTTPError as exc:
            log.warning(
                "robots_unreachable",
                extra={"domain": domain, "error_type": type(exc).__name__},
            )
            parser.disallow_all = True
        else:
            if response.status_code == 429:
                raise self._rate_limited(
                    robots_url,
                    RateLimited(
                        f"HTTP 429 ao baixar {robots_url}",
                        retry_after=parse_retry_after(response.headers.get("retry-after")),
                    ),
                )
            if 400 <= response.status_code < 500:
                parser.allow_all = True
            elif response.status_code >= 500:
                log.warning(
                    "robots_unreachable", extra={"domain": domain, "status": response.status_code}
                )
                parser.disallow_all = True
            else:
                parser.parse(response.text.splitlines())
        finally:
            self._mark(robots_url)
        self._robots[domain] = parser
        return parser
