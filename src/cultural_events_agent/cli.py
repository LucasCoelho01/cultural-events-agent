import argparse
import logging
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

from cultural_events_agent.config import Settings, load_settings
from cultural_events_agent.db import check_schema_contract, connect
from cultural_events_agent.errors import AgentError
from cultural_events_agent.fetching import PoliteFetcher
from cultural_events_agent.graph import RunResult, run_ingestion
from cultural_events_agent.llm import LlmClient, build_llm
from cultural_events_agent.logs import LOGGER_NAME, configure_logging
from cultural_events_agent.sources.base import Source
from cultural_events_agent.sources.sympla import SymplaSource

EXIT_USAGE = 64  # uso incorreto da CLI; o 2 é reservado para CONFIG_INVALID
PING_PROMPT = "Responda apenas com a palavra: pong"
DEBUG_DIR = Path(".debug")  # respostas brutas do modelo, só com LOG_LEVEL=DEBUG

# Fontes disponíveis para `run --source`; uma fonte nova entra aqui.
SOURCES: dict[str, Callable[[], Source]] = {"sympla": SymplaSource}

log = logging.getLogger(LOGGER_NAME)


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        self.print_usage(sys.stderr)
        self.exit(EXIT_USAGE, f"{self.prog}: erro: {message}\n")


def _city(value: str) -> tuple[str, str]:
    uf, separator, slug = value.partition("/")
    if not separator or len(uf) != 2 or not uf.isalpha() or not slug or "/" in slug:
        raise argparse.ArgumentTypeError("use <uf>/<slug>, por exemplo sp/sao-paulo")
    return uf.lower(), slug.lower()


def _positive_int(value: str) -> int:
    try:
        number = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError("deve ser um inteiro maior ou igual a 1") from None
    if number < 1:
        raise argparse.ArgumentTypeError("deve ser um inteiro maior ou igual a 1")
    return number


def build_parser() -> argparse.ArgumentParser:
    parser = _Parser(prog="cultural-events-agent")
    commands = parser.add_subparsers(dest="command", required=True)
    check = commands.add_parser(
        "check", help="valida a configuração, o banco, o contrato de schema e o modelo"
    )
    check.add_argument("--skip-llm", action="store_true", help="não faz a chamada mínima ao modelo")
    run = commands.add_parser("run", help="coleta os eventos de uma cidade numa fonte")
    run.add_argument("--source", required=True, choices=sorted(SOURCES), help="fonte a coletar")
    run.add_argument("--city", required=True, type=_city, help="cidade como <uf>/<slug>")
    run.add_argument("--limit", type=_positive_int, help="máximo de eventos processados")
    run.add_argument(
        "--dry-run", action="store_true", help="extrai e mostra o resultado sem gravar nada"
    )
    return parser


def run_check(
    settings: Settings,
    *,
    skip_llm: bool = False,
    llm_factory: Callable[[Settings], LlmClient] | None = None,
) -> None:
    """Confere banco, contrato e (opcionalmente) modelo, parando na primeira falha."""
    conn = connect(settings)
    try:
        log.info("db_connected", extra={"database": settings.database_url_masked})
        version = check_schema_contract(conn)
        log.info("schema_contract_ok", extra={"version": version})
    finally:
        conn.close()
    if skip_llm:
        log.info("llm_skipped")
        return
    (llm_factory or build_llm)(settings).invoke(PING_PROMPT)
    log.info("llm_ok", extra={"model": settings.anthropic_model})


def build_fetcher(settings: Settings) -> PoliteFetcher:
    return PoliteFetcher(settings)


def run_run(
    settings: Settings, *, source_code: str, city: tuple[str, str], limit: int | None, dry_run: bool
) -> RunResult:
    """Confere o banco e o contrato e executa o fluxo da fonte para a cidade."""
    source = SOURCES[source_code]()
    conn = connect(settings)
    fetcher = None
    try:
        log.info("db_connected", extra={"database": settings.database_url_masked})
        log.info("schema_contract_ok", extra={"version": check_schema_contract(conn)})
        conn.autocommit = True  # cada item abre a própria transação
        fetcher = build_fetcher(settings)
        return run_ingestion(
            conn,
            settings,
            source,
            build_llm(settings),
            fetcher,
            state_uf=city[0],
            city_slug=city[1],
            limit=limit,
            dry_run=dry_run,
            debug_dir=DEBUG_DIR if settings.log_level_number <= logging.DEBUG else None,
        )
    finally:
        if fetcher is not None:
            fetcher.close()
        conn.close()


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    configure_logging()
    try:
        settings = load_settings()
        configure_logging(settings.secret_values(), level=settings.log_level_number)
        log.info("config_ok", extra={"model": settings.anthropic_model})
        for name in settings.weak_secret_names():
            log.warning(
                "config_weak_secret", extra={"error_code": "CONFIG_WEAK_SECRET", "variable": name}
            )
        if args.command == "check":
            run_check(settings, skip_llm=args.skip_llm)
            log.info("check_ok")
        elif args.command == "run":
            run_run(
                settings,
                source_code=args.source,
                city=args.city,
                limit=args.limit,
                dry_run=args.dry_run,
            )
    except AgentError as exc:
        log.error(f"{args.command}_failed", extra={"error_code": exc.code, "detail": exc.message})
        return exc.exit_code
    except Exception as exc:  # erro inesperado
        # A mensagem passa pelo filtro de segredos do formatter; o traceback só com LOG_LEVEL=DEBUG.
        log.error(
            "unexpected_error",
            extra={
                "error_code": "UNEXPECTED_ERROR",
                "error_type": type(exc).__name__,
                "detail": str(exc),
            },
            exc_info=log.isEnabledFor(logging.DEBUG),
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
