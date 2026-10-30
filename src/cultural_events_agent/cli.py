import argparse
import logging
import sys
from collections.abc import Callable, Sequence

from cultural_events_agent.config import Settings, load_settings
from cultural_events_agent.db import check_schema_contract, connect
from cultural_events_agent.errors import AgentError
from cultural_events_agent.llm import LlmClient, build_llm
from cultural_events_agent.logs import LOGGER_NAME, configure_logging

EXIT_USAGE = 64  # uso incorreto da CLI; o 2 é reservado para CONFIG_INVALID
PING_PROMPT = "Responda apenas com a palavra: pong"

log = logging.getLogger(LOGGER_NAME)


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        self.print_usage(sys.stderr)
        self.exit(EXIT_USAGE, f"{self.prog}: erro: {message}\n")


def build_parser() -> argparse.ArgumentParser:
    parser = _Parser(prog="cultural-events-agent")
    commands = parser.add_subparsers(dest="command", required=True)
    check = commands.add_parser(
        "check", help="valida a configuração, o banco, o contrato de schema e o modelo"
    )
    check.add_argument("--skip-llm", action="store_true", help="não faz a chamada mínima ao modelo")
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


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    configure_logging()
    try:
        settings = load_settings()
        configure_logging(settings.secret_values())
        log.info("config_ok", extra={"model": settings.anthropic_model})
        for name in settings.weak_secret_names():
            log.warning(
                "config_weak_secret", extra={"error_code": "CONFIG_WEAK_SECRET", "variable": name}
            )
        if args.command == "check":
            run_check(settings, skip_llm=args.skip_llm)
            log.info("check_ok")
    except AgentError as exc:
        log.error("check_failed", extra={"error_code": exc.code, "detail": exc.message})
        return exc.exit_code
    except Exception as exc:  # erro inesperado: só o tipo, pois a mensagem pode ter segredos
        log.error(
            "unexpected_error",
            extra={"error_code": "UNEXPECTED_ERROR", "error_type": type(exc).__name__},
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
