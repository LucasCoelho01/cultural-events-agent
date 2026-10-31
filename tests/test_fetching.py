import httpx
import pytest

from cultural_events_agent.errors import FetchFailed, RobotsDisallowed
from cultural_events_agent.fetching import PoliteFetcher

ROBOTS_PERMISSIVO = "User-agent: *\nAllow: /\n"
ROBOTS_BLOQUEIA_EVENTOS = "User-agent: *\nDisallow: /evento/\n"
BASE = "https://exemplo.test"
LISTAGEM = f"{BASE}/eventos/sao-paulo-sp"
EVENTO = f"{BASE}/evento/show/1"


class Relogio:
    """Relógio e sleep falsos: dormir apenas adianta o tempo."""

    def __init__(self) -> None:
        self.agora = 1000.0
        self.pausas: list[float] = []

    def __call__(self) -> float:
        return self.agora

    def sleep(self, seconds: float) -> None:
        self.pausas.append(seconds)
        self.agora += seconds


class RendererFalso:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.urls: list[str] = []

    def render(self, url: str, timeout_seconds: float) -> str:
        self.urls.append(url)
        if self.error:
            raise self.error
        return f"<html>{url}</html>"

    def close(self) -> None:
        pass


def montar(
    make_settings,
    robots=(200, ROBOTS_PERMISSIVO),
    pagina=(200, "<html>lista</html>"),
    renderer=None,
    relogio=None,
    **settings,
):
    pedidos: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        pedidos.append(request)
        if request.url.path == "/robots.txt":
            return httpx.Response(robots[0], text=robots[1])
        return httpx.Response(pagina[0], text=pagina[1])

    relogio = relogio or Relogio()
    fetcher = PoliteFetcher(
        make_settings(**settings),
        http=httpx.Client(transport=httpx.MockTransport(handler)),
        renderer=renderer or RendererFalso(),
        clock=relogio,
        sleep=relogio.sleep,
    )
    return fetcher, pedidos, relogio


def test_listagem_baixada_com_user_agent(make_settings):
    fetcher, pedidos, _ = montar(make_settings)
    assert fetcher.get_listing(LISTAGEM) == "<html>lista</html>"
    assert {p.headers["user-agent"] for p in pedidos} == {
        "cultural-events-agent/0.1 (teste@example.com)"
    }


def test_robots_proibe_evento_sem_requisitar_a_pagina(make_settings):
    renderer = RendererFalso()
    fetcher, _, _ = montar(make_settings, robots=(200, ROBOTS_BLOQUEIA_EVENTOS), renderer=renderer)
    with pytest.raises(RobotsDisallowed):
        fetcher.get_event(EVENTO)
    assert renderer.urls == []
    assert fetcher.pages_fetched == 0


def test_robots_proibe_listagem(make_settings):
    fetcher, pedidos, _ = montar(make_settings, robots=(200, "User-agent: *\nDisallow: /\n"))
    with pytest.raises(RobotsDisallowed):
        fetcher.get_listing(LISTAGEM)
    assert [p.url.path for p in pedidos] == ["/robots.txt"]


def test_robots_404_permite_tudo(make_settings):
    fetcher, _, _ = montar(make_settings, robots=(404, "nao existe"))
    assert fetcher.get_listing(LISTAGEM)


def test_robots_503_proibe_tudo(make_settings):
    fetcher, pedidos, _ = montar(make_settings, robots=(503, "fora do ar"))
    with pytest.raises(RobotsDisallowed):
        fetcher.get_listing(LISTAGEM)
    assert [p.url.path for p in pedidos] == ["/robots.txt"]


def test_robots_inacessivel_proibe_tudo(make_settings):
    def handler(request):
        raise httpx.ConnectError("sem rede")

    relogio = Relogio()
    fetcher = PoliteFetcher(
        make_settings(),
        http=httpx.Client(transport=httpx.MockTransport(handler)),
        renderer=RendererFalso(),
        clock=relogio,
        sleep=relogio.sleep,
    )
    with pytest.raises(RobotsDisallowed):
        fetcher.get_listing(LISTAGEM)


def test_robots_lido_uma_vez_por_dominio(make_settings):
    fetcher, pedidos, _ = montar(make_settings)
    fetcher.get_listing(LISTAGEM)
    fetcher.get_event(EVENTO)
    fetcher.get_event(f"{BASE}/evento/outro/2")
    assert [p.url.path for p in pedidos].count("/robots.txt") == 1


def test_pausa_entre_requisicoes_do_mesmo_dominio(make_settings):
    fetcher, _, relogio = montar(make_settings, request_delay_seconds=2)
    fetcher.get_listing(LISTAGEM)
    fetcher.get_event(EVENTO)
    # robots -> listagem -> evento: duas pausas, cada uma completando 2 s
    assert len(relogio.pausas) == 2
    assert all(p == pytest.approx(2) for p in relogio.pausas)


def test_sem_pausa_na_primeira_requisicao(make_settings):
    fetcher, _, relogio = montar(make_settings, request_delay_seconds=2)
    fetcher.get_listing(LISTAGEM)
    assert len(relogio.pausas) == 1  # só entre o robots.txt e a listagem


def test_timeout_na_listagem_vira_fetch_failed(make_settings):
    def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text=ROBOTS_PERMISSIVO)
        raise httpx.ReadTimeout("lento")

    relogio = Relogio()
    fetcher = PoliteFetcher(
        make_settings(),
        http=httpx.Client(transport=httpx.MockTransport(handler)),
        renderer=RendererFalso(),
        clock=relogio,
        sleep=relogio.sleep,
    )
    with pytest.raises(FetchFailed):
        fetcher.get_listing(LISTAGEM)


def test_timeout_em_evento_vira_fetch_failed(make_settings):
    fetcher, _, _ = montar(make_settings, renderer=RendererFalso(error=TimeoutError("lento")))
    with pytest.raises(FetchFailed):
        fetcher.get_event(EVENTO)


def test_http_de_erro_vira_fetch_failed(make_settings):
    fetcher, _, _ = montar(make_settings, pagina=(500, "erro"))
    with pytest.raises(FetchFailed):
        fetcher.get_listing(LISTAGEM)


def test_limite_de_paginas(make_settings):
    fetcher, _, _ = montar(make_settings, max_pages_per_run=2)
    fetcher.get_listing(LISTAGEM)
    assert fetcher.pages_remaining == 1
    fetcher.get_event(EVENTO)
    assert fetcher.pages_remaining == 0
    with pytest.raises(FetchFailed, match="limite"):
        fetcher.get_event(f"{BASE}/evento/outro/2")
    assert fetcher.pages_fetched == 2


def test_robots_nao_conta_no_limite(make_settings):
    fetcher, _, _ = montar(make_settings, max_pages_per_run=1)
    fetcher.get_listing(LISTAGEM)
    assert fetcher.pages_fetched == 1


# ---- HTTP 429: espera por domínio, sem martelar o servidor ----


def montar_429(make_settings, respostas, relogio=None, renderer=None, **settings):
    """`respostas`: lista de (status, headers) consumida a cada página (a última se repete)."""
    pedidos = []
    fila = list(respostas)

    def handler(request):
        pedidos.append(request)
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text=ROBOTS_PERMISSIVO)
        status, headers = fila.pop(0) if len(fila) > 1 else fila[0]
        return httpx.Response(status, text="<html>x</html>", headers=headers)

    relogio = relogio or Relogio()
    fetcher = PoliteFetcher(
        make_settings(**settings),
        http=httpx.Client(transport=httpx.MockTransport(handler)),
        renderer=renderer or RendererFalso(),
        clock=relogio,
        sleep=relogio.sleep,
    )
    return fetcher, pedidos, relogio


def test_listagem_429_vira_rate_limited_com_retry_after(make_settings):
    from cultural_events_agent.errors import RateLimited

    fetcher, _, _ = montar_429(make_settings, [(429, {"Retry-After": "7"})])
    with pytest.raises(RateLimited) as exc:
        fetcher.get_listing(LISTAGEM)
    assert exc.value.retry_after == 7 and exc.value.code == "RATE_LIMITED"


def test_retry_after_que_nao_e_numero_e_ignorado(make_settings):
    from cultural_events_agent.errors import RateLimited

    fetcher, _, _ = montar_429(
        make_settings, [(429, {"Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"})]
    )
    with pytest.raises(RateLimited) as exc:
        fetcher.get_listing(LISTAGEM)
    assert exc.value.retry_after is None


class RendererLimitado(RendererFalso):
    """`plano`: True = responde 429 (com `retry_after`); False = página. Repete o último."""

    def __init__(self, plano: list[bool], retry_after: float | None = None) -> None:
        super().__init__()
        self.plano, self.retry_after = list(plano), retry_after

    def render(self, url, timeout_seconds):
        from cultural_events_agent.errors import RateLimited

        limitado = self.plano.pop(0) if len(self.plano) > 1 else self.plano[0]
        if limitado:
            raise RateLimited("HTTP 429", retry_after=self.retry_after)
        return "<html>ok</html>"


def espera_pendente(fetcher, relogio) -> float:
    """Quanto o domínio ainda precisa esperar, pelo cooldown, logo depois de um 429."""
    return fetcher._cooldown_until[BASE] - relogio()


def test_apos_429_o_dominio_espera_o_retry_after_antes_da_proxima_pagina(make_settings):
    from cultural_events_agent.errors import RateLimited

    renderer = RendererLimitado([True, False], retry_after=45)
    fetcher, _, relogio = montar_429(
        make_settings, [(200, {})], renderer=renderer, request_delay_seconds=2
    )
    with pytest.raises(RateLimited):
        fetcher.get_event(EVENTO)
    relogio.pausas.clear()
    assert fetcher.get_event(f"{BASE}/evento/outro/2") == "<html>ok</html>"
    assert relogio.pausas == [pytest.approx(45)]  # a espera longa vale mais que o intervalo de 2 s


def test_sem_retry_after_a_espera_dobra_a_cada_429_seguido_ate_o_teto(make_settings):
    from cultural_events_agent.errors import RateLimited

    renderer = RendererLimitado([True])
    fetcher, _, relogio = montar_429(make_settings, [(200, {})], renderer=renderer)
    esperas = []
    for i in range(5):
        with pytest.raises(RateLimited):
            fetcher.get_event(f"{BASE}/evento/e/{i}")
        esperas.append(espera_pendente(fetcher, relogio))
    assert esperas == [pytest.approx(v) for v in (30, 60, 120, 120, 120)]


def test_retry_after_acima_do_teto_e_limitado(make_settings):
    from cultural_events_agent.errors import RateLimited

    fetcher, _, relogio = montar_429(
        make_settings, [(200, {})], renderer=RendererLimitado([True], retry_after=3600)
    )
    with pytest.raises(RateLimited):
        fetcher.get_event(EVENTO)
    assert espera_pendente(fetcher, relogio) == pytest.approx(120)


def test_sucesso_zera_a_sequencia_de_429(make_settings):
    from cultural_events_agent.errors import RateLimited

    renderer = RendererLimitado([True, True, False, True])
    fetcher, _, relogio = montar_429(make_settings, [(200, {})], renderer=renderer)
    for i in range(2):
        with pytest.raises(RateLimited):
            fetcher.get_event(f"{BASE}/evento/e/{i}")
    assert espera_pendente(fetcher, relogio) == pytest.approx(60)  # segundo 429 seguido
    fetcher.get_event(f"{BASE}/evento/e/2")  # sucesso (depois de esperar)
    with pytest.raises(RateLimited):
        fetcher.get_event(f"{BASE}/evento/e/3")
    assert espera_pendente(fetcher, relogio) == pytest.approx(30)  # recomeçou do primeiro


def test_429_no_robots_txt_vira_rate_limited(make_settings):
    from cultural_events_agent.errors import RateLimited

    def handler(request):
        return httpx.Response(429, text="devagar", headers={"Retry-After": "5"})

    relogio = Relogio()
    fetcher = PoliteFetcher(
        make_settings(),
        http=httpx.Client(transport=httpx.MockTransport(handler)),
        renderer=RendererFalso(),
        clock=relogio,
        sleep=relogio.sleep,
    )
    with pytest.raises(RateLimited):
        fetcher.get_listing(LISTAGEM)
