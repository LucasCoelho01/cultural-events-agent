"""PlaywrightRenderer contra um servidor local (Chromium real, sem rede externa)."""

import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from playwright.sync_api import Error as PlaywrightError

from cultural_events_agent.errors import FetchFailed, RateLimited
from cultural_events_agent.fetching import PlaywrightRenderer

PAGINA_OK = """<html><body><h1>Evento</h1>
<div data-testid="ticket-grid"><div data-testid="ticket-grid-item">R$ 10,00</div></div>
</body></html>"""

# Nunca fica ociosa: refaz uma requisição a cada 100 ms, como rastreadores e anúncios.
PAGINA_SEM_IDLE = """<html><body><h1>Sempre ocupada</h1>
<div data-testid="ticket-grid-item">R$ 20,00</div>
<script>setInterval(() => fetch('/ping?t=' + Date.now()), 100);</script></body></html>"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # silencioso
        pass

    def do_GET(self):
        rotas = {
            "/ok": (200, PAGINA_OK, {}),
            "/limitada": (429, "<html>devagar</html>", {"Retry-After": "7"}),
            "/limitada-sem-header": (429, "<html>devagar</html>", {}),
            "/quebrada": (500, "<html>erro</html>", {}),
            "/sem-idle": (200, PAGINA_SEM_IDLE, {}),
            "/ping": (200, "ok", {}),
        }
        status, corpo, headers = rotas.get(self.path.split("?")[0], (404, "nada", {}))
        dados = corpo.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(dados)))
        for chave, valor in headers.items():
            self.send_header(chave, valor)
        self.end_headers()
        self.wfile.write(dados)


@pytest.fixture(scope="module")
def servidor():
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()


@pytest.fixture(scope="module")
def renderer():
    renderer = PlaywrightRenderer("cultural-events-agent/teste")
    try:
        renderer._ensure_started()
    except Exception as exc:  # Chromium não instalado
        pytest.skip(f"Chromium do Playwright indisponível: {exc}")
    yield renderer
    renderer.close()


def test_pagina_normal_devolve_o_html_com_o_ingresso(renderer, servidor):
    html = renderer.render(f"{servidor}/ok", 15)
    assert "R$ 10,00" in html and 'data-testid="ticket-grid"' in html


def test_429_e_detectado_em_segundos_com_retry_after(renderer, servidor):
    inicio = time.monotonic()
    with pytest.raises(RateLimited) as exc:
        renderer.render(f"{servidor}/limitada", 30)
    assert time.monotonic() - inicio < 5
    assert exc.value.retry_after == 7


def test_429_sem_retry_after(renderer, servidor):
    with pytest.raises(RateLimited) as exc:
        renderer.render(f"{servidor}/limitada-sem-header", 30)
    assert exc.value.retry_after is None


def test_http_de_erro_vira_fetch_failed_sem_ser_rate_limited(renderer, servidor):
    with pytest.raises(FetchFailed) as exc:
        renderer.render(f"{servidor}/quebrada", 30)
    assert not isinstance(exc.value, RateLimited) and "HTTP 500" in exc.value.message


def test_pagina_que_nunca_fica_ociosa_nao_estoura_o_timeout(renderer, servidor):
    """Com networkidle levaria o timeout inteiro; agora termina quando o ingresso aparece."""
    inicio = time.monotonic()
    html = renderer.render(f"{servidor}/sem-idle", 30)
    assert time.monotonic() - inicio < 8
    assert "R$ 20,00" in html


def test_apos_um_erro_do_navegador_o_proximo_render_reabre_o_navegador(renderer, servidor):
    renderer._browser.close()  # simula o TargetClosedError do run real
    with pytest.raises(PlaywrightError):
        renderer.render(f"{servidor}/ok", 15)
    assert renderer._context is None  # foi descartado
    assert "R$ 10,00" in renderer.render(f"{servidor}/ok", 15)  # reabre sozinho


def test_close_com_falha_no_meio_nao_deixa_o_playwright_rodando(monkeypatch):
    """browser.close() lança: o playwright ainda é parado e o próximo render funciona."""
    eventos = []

    class Resposta:
        status = 200
        headers = {}

    class Pagina:
        def goto(self, *args, **kwargs):
            return Resposta()

        def wait_for_selector(self, *args, **kwargs):
            pass

        def content(self):
            return "<html>ok</html>"

        def close(self):
            pass

    class Contexto:
        def new_page(self):
            return Pagina()

        def close(self):
            eventos.append("context")

    class Navegador:
        def new_context(self, **kwargs):
            return Contexto()

        def close(self):
            eventos.append("browser")
            raise RuntimeError("navegador já caiu")

    class Playwright:
        def __init__(self):
            self.chromium = self

        def launch(self):
            return Navegador()

        def stop(self):
            eventos.append("playwright")

    class Gerenciador:
        def start(self):
            return Playwright()

    import playwright.sync_api as sync_api

    monkeypatch.setattr(sync_api, "sync_playwright", Gerenciador)
    renderer = PlaywrightRenderer("ua")
    assert renderer.render("http://x", 5) == "<html>ok</html>"
    renderer.close()
    assert eventos == ["context", "browser", "playwright"]
    assert renderer._playwright is renderer._browser is renderer._context is None
    assert renderer.render("http://x", 5) == "<html>ok</html>"  # start() de novo funciona
