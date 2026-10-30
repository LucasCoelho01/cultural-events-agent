import json
import logging

from cultural_events_agent.logs import LOGGER_NAME, configure_logging

API_KEY = "sk-ant-chave-secreta-1234"
DB_PASSWORD = "senha-do-banco-9876"


def lines(capsys) -> list[dict]:
    err = capsys.readouterr().err
    return [json.loads(line) for line in err.splitlines() if line.strip()]


def test_linha_json_com_campos_basicos(capsys):
    configure_logging()
    logging.getLogger(LOGGER_NAME).error("check_failed", extra={"error_code": "DB_UNAVAILABLE"})
    (entry,) = lines(capsys)
    assert entry["event"] == "check_failed"
    assert entry["level"] == "ERROR"
    assert entry["error_code"] == "DB_UNAVAILABLE"
    assert entry["timestamp"].endswith("+00:00")


def test_extras_numericos_continuam_numericos(capsys):
    configure_logging()
    logging.getLogger(LOGGER_NAME).info("passo", extra={"tentativas": 3})
    assert lines(capsys)[0]["tentativas"] == 3


def test_segredos_sao_mascarados(capsys):
    configure_logging([API_KEY, DB_PASSWORD])
    logging.getLogger(LOGGER_NAME).error(
        f"falhou com {API_KEY}", extra={"detail": f"postgresql://u:{DB_PASSWORD}@h/db"}
    )
    raw = capsys.readouterr().err
    assert API_KEY not in raw
    assert DB_PASSWORD not in raw
    entry = json.loads(raw)
    assert "***" in entry["event"]
    assert "***" in entry["detail"]


def test_reconfigurar_nao_duplica_linhas(capsys):
    configure_logging()
    configure_logging()
    logging.getLogger(LOGGER_NAME).info("uma_vez")
    assert len(lines(capsys)) == 1


def test_saida_e_ascii_mas_json_preserva_acentos(capsys):
    configure_logging()
    logging.getLogger(LOGGER_NAME).error("falhou", extra={"detail": "não foi possível"})
    raw = capsys.readouterr().err
    assert raw.isascii()
    assert json.loads(raw)["detail"] == "não foi possível"


def test_segredo_curto_nao_e_mascarado(capsys):
    configure_logging(["abc", "1234567", API_KEY])
    logging.getLogger(LOGGER_NAME).info("abcdef 1234567 abc", extra={"detail": API_KEY})
    entry = json.loads(capsys.readouterr().err)
    assert entry["event"] == "abcdef 1234567 abc"
    assert entry["detail"] == "***"


def test_segredo_com_exatamente_8_caracteres_e_mascarado(capsys):
    configure_logging(["12345678"])
    logging.getLogger(LOGGER_NAME).info("senha 12345678 aqui")
    assert json.loads(capsys.readouterr().err)["event"] == "senha *** aqui"
