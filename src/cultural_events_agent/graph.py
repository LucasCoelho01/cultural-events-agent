"""Fluxo de ingestão em LangGraph: descobrir, baixar, triar, extrair, mapear, deduplicar, gravar.

A descoberta roda uma vez; as demais etapas processam um item de cada vez, e a falha de um item é
registrada sem interromper os outros. A triagem compara o hash dos dados brutos com o vínculo e
encerra o item inalterado sem chamar o modelo.
"""

import json
import logging
import re
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TextIO, TypedDict
from zoneinfo import ZoneInfo

import psycopg
from langgraph.graph import END, StateGraph

from cultural_events_agent.config import Settings
from cultural_events_agent.db import (
    RunCounts,
    RunError,
    RunStatus,
    close_run,
    open_run,
    translate_db_errors,
)
from cultural_events_agent.errors import (
    AgentError,
    CityInactive,
    CityNotFound,
    DbContractViolation,
    DbUnavailable,
    ExtractionInvalid,
    FetchFailed,
    ItemFailed,
    LlmUnavailable,
    RateLimited,
    RobotsDisallowed,
    SourceNotFound,
    VersionConflict,
)
from cultural_events_agent.extraction import (
    SKIP_NOT_CULTURAL,
    SKIP_TEST_EVENT,
    ItemSkipped,
    NormalizedEvent,
    extract_event,
)
from cultural_events_agent.fetching import PageFetcher
from cultural_events_agent.fingerprint import content_hash, is_test_event
from cultural_events_agent.llm import LlmClient
from cultural_events_agent.logs import LOGGER_NAME
from cultural_events_agent.mapping import MappedEvent, load_categories, map_event
from cultural_events_agent.sources.base import DiscoveredItem, Source
from cultural_events_agent.writer import Outcome, plan_item, read_link, touch_link, write_item

log = logging.getLogger(LOGGER_NAME)

SKIPPED = "skipped"
FAILED = "failed"
UNCHANGED = "unchanged"
STEPS_PER_ITEM = 10
MAX_RATE_LIMITED_IN_A_ROW = 3  # 429 seguidos: o servidor está nos limitando, a execução para


class RunState(TypedDict, total=False):
    queue: list[DiscoveredItem]
    item: DiscoveredItem | None
    html: str | None
    raw_hash: str | None
    event: NormalizedEvent | None
    mapped: MappedEvent | None
    plan: str | None
    status: str | None  # preenchido quando o item termina (em qualquer etapa)


@dataclass
class RunResult:
    run_id: int | None
    status: RunStatus
    counts: RunCounts
    skipped: dict[str, int] = field(default_factory=dict)
    errors: list[RunError] = field(default_factory=list)
    discovered: int = 0
    pending: int = 0  # descobertos e não processados (limite de páginas ou execução abortada)

    @property
    def skipped_total(self) -> int:
        return sum(self.skipped.values())


@dataclass(frozen=True)
class CityRow:
    id: int
    name: str
    timezone: str
    active: bool


def resolve_city(conn: psycopg.Connection, state_uf: str, city_slug: str) -> CityRow:
    """A cidade pedida (`uf` + `slug`), que precisa existir e estar ativa."""
    row = conn.execute(
        "SELECT id, name, timezone, active FROM city WHERE state_uf = %s AND slug = %s",
        (state_uf.upper(), city_slug.lower()),
    ).fetchone()
    if row is None:
        raise CityNotFound(f"a cidade {state_uf.lower()}/{city_slug} não existe em city")
    city = CityRow(*row)
    if not city.active:
        raise CityInactive(f"a cidade {state_uf.lower()}/{city_slug} está inativa")
    return city


def resolve_source_id(conn: psycopg.Connection, code: str) -> int:
    row = conn.execute("SELECT id FROM source WHERE code = %s AND active", (code,)).fetchone()
    if row is None:
        raise SourceNotFound(f"a fonte '{code}' não existe em source ou está inativa")
    return row[0]


class _Ingestion:
    """Estado da execução e os nós do grafo (uma instância por execução)."""

    def __init__(
        self,
        conn: psycopg.Connection,
        source: Source,
        llm: LlmClient,
        fetcher: PageFetcher,
        *,
        city: CityRow,
        state_uf: str,
        city_slug: str,
        source_id: int,
        run_id: int | None,
        categories: dict[str, int],
        limit: int | None,
        max_items: int,
        dry_run: bool,
        now: Callable[[], datetime],
        out: TextIO,
        secrets: list[str] | None = None,
        debug_dir: Path | None = None,
    ) -> None:
        self.secrets = secrets or []
        self.debug_dir = debug_dir
        self.discovered = 0
        self.rate_limited_in_a_row = 0
        self.stopped_by: RateLimited | None = None
        self.buckets: dict[str, str] = {}  # external_id -> created/updated/unchanged/skipped/failed
        self.conn, self.source, self.llm, self.fetcher = conn, source, llm, fetcher
        self.city, self.state_uf, self.city_slug = city, state_uf, city_slug
        self.source_id, self.run_id, self.categories = source_id, run_id, categories
        self.limit, self.max_items, self.dry_run, self.now, self.out = (
            limit,
            max_items,
            dry_run,
            now,
            out,
        )
        self.timezone = ZoneInfo(city.timezone)
        self.counts = {"created": 0, "updated": 0, "unchanged": 0, "failed": 0}
        self.skipped: dict[str, int] = {}
        self.errors: list[RunError] = []

    # ----- grafo -----

    def build(self):
        graph = StateGraph(RunState)
        graph.add_node("discover", self._discover)  # erro da listagem aborta a execução
        for name in ("fetch", "triage", "extract", "map", "dedupe", "write", "report"):
            graph.add_node(name, self._guarded(name, getattr(self, f"_{name}")))
        graph.add_node("next_item", lambda state: {})
        graph.set_entry_point("discover")
        graph.add_conditional_edges("discover", self._more, {"fetch": "fetch", END: END})
        graph.add_conditional_edges("fetch", self._after("triage"), self._targets("triage"))
        graph.add_conditional_edges("triage", self._after("extract"), self._targets("extract"))
        graph.add_conditional_edges("extract", self._after("map"), self._targets("map"))
        graph.add_conditional_edges("map", self._after("dedupe"), self._targets("dedupe"))
        graph.add_conditional_edges("dedupe", self._to_write_or_report, self._write_targets())
        graph.add_edge("write", "next_item")
        graph.add_edge("report", "next_item")
        graph.add_conditional_edges("next_item", self._more, {"fetch": "fetch", END: END})
        return graph.compile()

    @staticmethod
    def _targets(step: str) -> dict[str, str]:
        return {step: step, "next_item": "next_item"}

    @staticmethod
    def _write_targets() -> dict[str, str]:
        return {"write": "write", "report": "report", "next_item": "next_item"}

    @staticmethod
    def _after(step: str) -> Callable[[RunState], str]:
        return lambda state: "next_item" if state.get("status") else step

    def _to_write_or_report(self, state: RunState) -> str:
        if state.get("status"):
            return "next_item"
        return "report" if self.dry_run else "write"

    def _more(self, state: RunState) -> str:
        if not state.get("queue"):
            return END
        if self.fetcher.pages_remaining <= 0:
            log.warning("page_limit_reached", extra={"pending_items": len(state["queue"])})
            return END
        return "fetch"

    # ----- registro de cada item -----

    def _finish(self, item: DiscoveredItem, status: str, **info: Any) -> dict[str, Any]:
        """Encerra o item num único balde; contar é o último passo, para não contar duas vezes."""
        reason = info.get("reason")
        if status == SKIPPED:
            extra = {"external_id": item.external_id, "reason": reason}
            if info.get("detail"):
                extra["detail"] = info["detail"]
            log.info("item_skipped", extra=extra)
        elif status != FAILED:
            log.info("item_done", extra={"external_id": item.external_id, "status": status})
        if self.dry_run and status in (SKIPPED, FAILED, UNCHANGED):
            self._print(
                {"external_id": item.external_id, "url": item.url, "action": status, **info}
            )
        self._count(item, status, reason)
        return {"status": status}

    def _count(self, item: DiscoveredItem, status: str, reason: str | None = None) -> None:
        if item.external_id in self.buckets:
            return
        self.buckets[item.external_id] = status
        if status == SKIPPED:
            self.skipped[reason] = self.skipped.get(reason, 0) + 1
            self.counts["unchanged"] += 1  # ingestion_run não tem coluna skipped
        else:
            self.counts[status] += 1

    def _fail(self, item: DiscoveredItem, exc: AgentError) -> dict[str, Any]:
        detail = self._scrub(exc.message)
        self.errors.append(RunError(external_id=item.external_id, message=f"{exc.code}: {detail}"))
        log.warning(
            "item_failed",
            extra={"external_id": item.external_id, "error_code": exc.code, "detail": detail},
        )
        return self._finish(item, FAILED, error_code=exc.code, detail=detail)

    def _print(self, payload: dict[str, Any]) -> None:
        self.out.write(json.dumps(payload, ensure_ascii=False, default=str) + "\n")
        self.out.flush()

    # ----- exceção inesperada num item -----

    def _scrub(self, text: str) -> str:
        for secret in self.secrets:
            text = text.replace(secret, "***")
        return text

    def _guarded(self, name: str, node: Callable[[RunState], dict[str, Any]]):
        """Exceção inesperada num item vira falha daquele item (ITEM_FAILED) e a execução segue.

        Só DbUnavailable e LlmUnavailable abortam (todos os itens falhariam do mesmo jeito).
        """

        def run(state: RunState) -> dict[str, Any]:
            try:
                return node(state)
            except (DbUnavailable, LlmUnavailable):
                raise
            except Exception as exc:
                fetching = name == "fetch"
                item = state["queue"][0] if fetching else state["item"]
                failure = (
                    exc
                    if isinstance(exc, AgentError)
                    else ItemFailed(f"{type(exc).__name__}: {self._scrub(str(exc))}")
                )
                outcome = self._fail(item, failure)
                if not fetching:
                    return outcome
                return {
                    "queue": list(state["queue"][1:]),
                    "item": item,
                    "html": None,
                    "raw_hash": None,
                    "event": None,
                    "mapped": None,
                    "plan": None,
                    **outcome,
                }

        return run

    def _scrub_deep(self, value: Any) -> Any:
        if isinstance(value, str):
            return self._scrub(value)
        if isinstance(value, dict):
            return {key: self._scrub_deep(item) for key, item in value.items()}
        if isinstance(value, list):
            return [self._scrub_deep(item) for item in value]
        return value

    def _dump_raw(self, item: DiscoveredItem, exc: AgentError) -> None:
        """Com LOG_LEVEL=DEBUG, guarda a resposta bruta do modelo em .debug/<external_id>.json."""
        raw = getattr(self.llm, "last_raw", None)
        if self.debug_dir is None or raw is None:
            return
        name = re.sub(r"[^A-Za-z0-9._-]", "_", item.external_id)
        path = self.debug_dir / f"{name}.json"
        payload = {
            "external_id": item.external_id,
            "url": item.url,
            "error": exc.message,
            "raw": raw,
        }
        try:
            self.debug_dir.mkdir(parents=True, exist_ok=True)
            text = json.dumps(self._scrub_deep(payload), ensure_ascii=False, indent=2, default=str)
            path.write_text(text, encoding="utf-8")
        except OSError as os_error:
            log.warning("debug_dump_failed", extra={"error_type": type(os_error).__name__})
            return
        log.debug("raw_response_saved", extra={"external_id": item.external_id, "path": str(path)})

    # ----- nós -----

    def _discover(self, state: RunState) -> dict[str, Any]:
        wanted = self.limit if self.limit is not None else None
        found: dict[str, DiscoveredItem] = {}
        page = 1
        while True:
            url = self.source.listing_url(self.state_uf, self.city_slug, page)
            try:
                html = self.fetcher.get_listing(url)
            except (RobotsDisallowed, FetchFailed):
                if page == 1:
                    raise  # sem a primeira listagem não há execução
                log.warning("listing_page_failed", extra={"page": page})
                break
            new = [i for i in self.source.parse_listing(html) if i.external_id not in found]
            for item in new:
                found[item.external_id] = item
            enough = wanted is not None and len(found) >= wanted
            if enough or not new or wanted is None or self.fetcher.pages_remaining <= 1:
                break
            page += 1
        items = list(found.values())
        if wanted is not None:
            items = items[:wanted]
        self.discovered = len(items)
        log.info("discovered", extra={"items": len(items), "pages": page})
        return {"queue": items}

    def _fetch(self, state: RunState) -> dict[str, Any]:
        queue = list(state["queue"])
        item = queue.pop(0)
        reset: dict[str, Any] = {
            "queue": queue,
            "item": item,
            "html": None,
            "raw_hash": None,
            "event": None,
            "mapped": None,
            "plan": None,
            "status": None,
        }
        if is_test_event(item.slug):
            return {**reset, **self._finish(item, SKIPPED, reason=SKIP_TEST_EVENT)}
        link = read_link(self.conn, self.source_id, item.external_id)
        if link is not None and link.is_tombstone:
            return {**reset, **self._finish(item, UNCHANGED, reason="tombstone")}
        try:
            html = self.fetcher.get_event(item.url)
        except RateLimited as exc:
            self.rate_limited_in_a_row += 1
            outcome = {**reset, **self._fail(item, exc)}
            if self.rate_limited_in_a_row >= MAX_RATE_LIMITED_IN_A_ROW:
                self.stopped_by = exc
                log.error(
                    "run_stopped_rate_limited",
                    extra={"consecutive": self.rate_limited_in_a_row, "pending": len(queue)},
                )
                outcome["queue"] = []  # o resto fica pendente para a próxima execução
            return outcome
        except (RobotsDisallowed, FetchFailed) as exc:
            self.rate_limited_in_a_row = 0
            return {**reset, **self._fail(item, exc)}
        self.rate_limited_in_a_row = 0
        return {**reset, "html": html}

    def _triage(self, state: RunState) -> dict[str, Any]:
        item, html = state["item"], state["html"]
        raw = self.source.hash_input(html)
        if not raw.get("pricing"):
            log.warning("pricing_section_missing", extra={"external_id": item.external_id})
        raw_hash = content_hash(raw)
        link = read_link(self.conn, self.source_id, item.external_id)
        if link is not None and link.content_hash == raw_hash:
            try:
                if not self.dry_run and self.run_id is not None:
                    touch_link(self.conn, link.id, self.run_id)
            except DbContractViolation as exc:
                return self._fail(item, exc)
            return self._finish(item, UNCHANGED, reason="same_hash")
        return {"raw_hash": raw_hash}

    def _extract(self, state: RunState) -> dict[str, Any]:
        item, html = state["item"], state["html"]
        self.llm.last_raw = None  # só vale a resposta deste item
        try:
            event = extract_event(
                self.source.structured_data(html, self.timezone),
                llm=self.llm,
                categories=list(self.categories),
                page_text=self.source.page_text(html),
                pricing_text=self.source.pricing_text(html),
                timezone=self.timezone,
                now=self.now(),
                item_url=item.url,
            )
        except ItemSkipped as skip:
            detail = self._scrub(skip.detail) if skip.detail else None
            if skip.reason == SKIP_NOT_CULTURAL:
                self._warn_if_already_stored(item, detail)
            return self._finish(item, SKIPPED, reason=skip.reason, detail=detail)
        except ExtractionInvalid as exc:
            self._dump_raw(item, exc)
            return self._fail(item, exc)
        return {"event": event}

    def _warn_if_already_stored(self, item: DiscoveredItem, detail: str | None) -> None:
        """Evento já gravado que passou a ser não cultural: não apaga, não despublica, não altera.

        Só registra no log, para uma decisão humana depois.
        """
        link = read_link(self.conn, self.source_id, item.external_id)
        if link is not None and link.event_id is not None:
            log.warning(
                "existing_event_not_cultural",
                extra={
                    "external_id": item.external_id,
                    "event_id": link.event_id,
                    "reason": SKIP_NOT_CULTURAL,
                    "detail": detail or "-",
                },
            )

    def _map(self, state: RunState) -> dict[str, Any]:
        try:
            with translate_db_errors():
                mapped = map_event(
                    self.conn, state["event"], city_id=self.city.id, categories=self.categories
                )
        except DbContractViolation as exc:
            return self._fail(state["item"], exc)
        return {"mapped": mapped}

    def _dedupe(self, state: RunState) -> dict[str, Any]:
        try:
            with translate_db_errors():
                plan = plan_item(
                    self.conn,
                    source_id=self.source_id,
                    item=state["item"],
                    raw_hash=state["raw_hash"],
                    mapped=state["mapped"],
                    city_id=self.city.id,
                )
        except DbContractViolation as exc:
            return self._fail(state["item"], exc)
        return {"plan": plan}

    def _write(self, state: RunState) -> dict[str, Any]:
        item = state["item"]
        try:
            outcome = write_item(
                self.conn,
                source_id=self.source_id,
                run_id=self.run_id,
                item=item,
                raw_hash=state["raw_hash"],
                mapped=state["mapped"],
                city_id=self.city.id,
            )
        except (DbContractViolation, VersionConflict) as exc:
            return self._fail(item, exc)
        return self._finish(item, outcome.value if isinstance(outcome, Outcome) else outcome)

    def _report(self, state: RunState) -> dict[str, Any]:
        item, mapped = state["item"], state["mapped"]
        venue: dict[str, Any] | None = None
        if mapped.venue_id is not None:
            venue = {"id": mapped.venue_id}
        elif mapped.venue_to_create is not None:
            venue = {"would_create": mapped.venue_to_create.name}
        self._print(
            {
                "external_id": item.external_id,
                "url": item.url,
                "action": state["plan"],
                "category": mapped.category_slug,
                "venue": venue,
                "event": mapped.event.model_dump(mode="json"),
            }
        )
        status = {"create": "created", "update": "updated"}.get(state["plan"], UNCHANGED)
        self._count(item, status)
        return {"status": status}


def run_ingestion(
    conn: psycopg.Connection,
    settings: Settings,
    source: Source,
    llm: LlmClient,
    fetcher: PageFetcher,
    *,
    state_uf: str,
    city_slug: str,
    limit: int | None = None,
    dry_run: bool = False,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
    out: TextIO | None = None,
    debug_dir: Path | None = None,
) -> RunResult:
    """Valida cidade e fonte, executa o grafo e fecha o `ingestion_run` (se não for dry-run).

    O log `run_finished` sai sempre no fim, com os contadores, inclusive no `--dry-run` e quando a
    execução é abortada. `conn` deve estar em autocommit: cada item abre a própria transação.
    Com `dry_run`, a sessão fica somente leitura, então o próprio banco recusa qualquer escrita.
    `debug_dir` (LOG_LEVEL=DEBUG) recebe a resposta bruta do modelo dos itens EXTRACTION_INVALID.
    """
    ingestion: _Ingestion | None = None
    run_id: int | None = None
    fatal: AgentError | None = None
    try:
        if dry_run:
            # `conn.read_only` só vale em BEGIN; em autocommit é o default da sessão que protege.
            conn.execute("SET default_transaction_read_only = on")
        city = resolve_city(conn, state_uf, city_slug)
        source_id = resolve_source_id(conn, source.code)
        categories = load_categories(conn)
        run_id = None if dry_run else open_run(conn, source_id)
        max_items = limit if limit is not None else settings.max_pages_per_run
        ingestion = _Ingestion(
            conn,
            source,
            llm,
            fetcher,
            city=city,
            state_uf=state_uf,
            city_slug=city_slug,
            source_id=source_id,
            run_id=run_id,
            categories=categories,
            limit=limit,
            max_items=max_items,
            dry_run=dry_run,
            now=now,
            out=out or sys.stdout,
            secrets=settings.secret_values(),
            debug_dir=debug_dir,
        )
        ingestion.build().invoke(
            {"queue": []}, config={"recursion_limit": 20 + STEPS_PER_ITEM * (max_items + 1)}
        )
        if ingestion.stopped_by is not None:
            raise RateLimited(
                f"o servidor respondeu 429 em {MAX_RATE_LIMITED_IN_A_ROW} páginas seguidas; "
                "execução interrompida (aumente REQUEST_DELAY_SECONDS e tente mais tarde)"
            )
    except AgentError as exc:
        fatal = exc
    except BaseException:
        _finish_run(conn, run_id, RunStatus.FAILED, ingestion, dry_run, "UNEXPECTED_ERROR")
        raise
    if fatal is not None:
        message = ingestion._scrub(str(fatal)) if ingestion else str(fatal)
        if ingestion is not None:
            ingestion.errors.append(RunError(external_id="-", message=message))
        status = RunStatus.FAILED
    elif ingestion is not None and ingestion.counts["failed"]:
        status = RunStatus.PARTIAL
    else:
        status = RunStatus.SUCCESS
    result = _finish_run(
        conn, run_id, status, ingestion, dry_run, fatal.code if fatal is not None else None
    )
    if fatal is not None:
        raise fatal
    return result


def _finish_run(
    conn: psycopg.Connection,
    run_id: int | None,
    status: RunStatus,
    ingestion: _Ingestion | None,
    dry_run: bool,
    error_code: str | None,
) -> RunResult:
    """Fecha o `ingestion_run` (se houver) e emite `run_finished` com os contadores."""
    counts = ingestion.counts if ingestion else {}
    result = RunResult(
        run_id=run_id,
        status=status,
        counts=RunCounts(**counts),
        skipped=dict(ingestion.skipped) if ingestion else {},
        errors=list(ingestion.errors) if ingestion else [],
        discovered=ingestion.discovered if ingestion else 0,
        pending=(ingestion.discovered - len(ingestion.buckets)) if ingestion else 0,
    )
    if run_id is not None:
        errors = list(result.errors)
        if ingestion is None or (error_code == "UNEXPECTED_ERROR"):
            errors.append(RunError(external_id="-", message="erro inesperado na execução"))
        try:
            close_run(conn, run_id, status, result.counts, errors)
        except Exception as exc:  # não esconde o erro original da execução
            log.error("run_close_failed", extra={"error_type": type(exc).__name__})
    log_run_finished(result, dry_run=dry_run, error_code=error_code)
    return result


def log_run_finished(result: RunResult, *, dry_run: bool, error_code: str | None = None) -> None:
    """Cada item descoberto cai num só balde: created, updated, unchanged, skipped ou failed.

    `items_unchanged` exclui os `skipped` (no `ingestion_run`, `unchanged` os inclui).
    `items_pending` são os descobertos não processados (limite de páginas ou execução abortada).
    """
    extra: dict[str, Any] = {
        "status": result.status.value,
        "dry_run": dry_run,
        "run_id": result.run_id if result.run_id is not None else "-",
        "items_discovered": result.discovered,
        "items_created": result.counts.created,
        "items_updated": result.counts.updated,
        "items_unchanged": result.counts.unchanged - result.skipped_total,
        "items_skipped": result.skipped_total,
        "items_failed": result.counts.failed,
        "items_pending": result.pending,
    }
    extra.update({f"items_skipped_{reason.lower()}": n for reason, n in result.skipped.items()})
    if error_code:
        extra["error_code"] = error_code
    log.info("run_finished", extra=extra)
