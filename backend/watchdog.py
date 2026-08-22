"""Watchdog anti-queda — supervisor que mantém a API viva.

Uso local (iniciar.bat / start.bat):

    python -m backend.watchdog

O que ele faz:
  1. Sobe o uvicorn como SUBPROCESSO (filho morre -> pai percebe na hora).
  2. A cada 15s consulta GET /health; se o processo morreu OU a saúde ficou
     "red" por 90s seguidos (deadlock não mata o processo, mas derruba a saúde),
     reinicia o backend.
  3. Backoff progressivo (5s -> 10s -> ... -> teto de 120s) para não crash-loopar
     quando a causa é persistente (ex.: porta ocupada). O backoff zera depois de
     10 min estáveis. NUNCA desiste de vez — a missão é "caiu, volta".
  4. Encaminha o sinal de parada (Ctrl+C / fecha janela) para o filho, então
     parar o watchdog para o backend junto.

Variáveis: WATCHDOG_HOST (0.0.0.0), WATCHDOG_PORT (8000),
WATCHDOG_HEALTH_URL (default http://127.0.0.1:<porta>/health).
"""
from __future__ import annotations

import json
import logging
import os
import signal
import subprocess
import sys
import time
import urllib.request

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s watchdog: %(message)s",
)
logger = logging.getLogger("studio.watchdog")

HOST = os.getenv("WATCHDOG_HOST", "0.0.0.0")
PORT = int(os.getenv("WATCHDOG_PORT", "8000"))
HEALTH_URL = os.getenv("WATCHDOG_HEALTH_URL", f"http://127.0.0.1:{PORT}/health")

_CHECK_EVERY_S = 15.0
# Saúde "red" sustentada por isto => reinicia (deadlock do scheduler/worker
# não mata o processo; sem esta checagem a API ficaria zumbi para sempre).
_RED_FOR_RESTART_S = 90.0
_BACKOFF_START_S = 5.0
_BACKOFF_CAP_S = 120.0
# Se o filho sobreviveu isto, consideramos a saída "limpa o suficiente" e o
# backoff volta ao início (um crash a cada hora não merece espera de 2 min).
_STABLE_RESET_S = 600.0


def _health_status() -> str | None:
    """'green'|'yellow'|'red' ou None se a API nem respondeu."""
    try:
        with urllib.request.urlopen(HEALTH_URL, timeout=5) as resp:
            return json.loads(resp.read().decode()).get("status")
    except Exception:  # noqa: BLE001 — qualquer falha (DNS, timeout, JSON) = "não respondeu"
        return None


def _spawn() -> subprocess.Popen:
    cmd = [
        sys.executable, "-m", "uvicorn", "backend.main:app",
        "--host", HOST, "--port", str(PORT),
    ]
    logger.info("Subindo backend: %s", " ".join(cmd))
    # O filho herda stdout/stderr — os logs do backend aparecem nesta mesma
    # janela, como antes do watchdog existir.
    return subprocess.Popen(cmd)


def main() -> int:
    backoff = _BACKOFF_START_S
    red_since: float | None = None
    child = _spawn()
    started_at = time.monotonic()

    def _stop(*_args) -> None:
        logger.info("Encerrando backend (pid=%s)...", child.pid)
        child.terminate()
        try:
            child.wait(timeout=10)
        except subprocess.TimeoutExpired:
            child.kill()
        raise SystemExit(0)

    signal.signal(signal.SIGINT, _stop)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _stop)

    restarts = 0
    while True:
        time.sleep(_CHECK_EVERY_S)
        exit_code = child.poll()

        if exit_code is None:
            # Vivo: checa a saúde. "yellow" (sem Redis, sem edge-tts) NÃO
            # reinicia — só "red" sustentado ou API muda.
            status = _health_status()
            if status == "red":
                red_since = red_since or time.monotonic()
                if time.monotonic() - red_since >= _RED_FOR_RESTART_S:
                    logger.warning("Saúde 'red' por %.0fs — reiniciando backend.", _RED_FOR_RESTART_S)
                    child.terminate()
                    try:
                        child.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        child.kill()
                    exit_code = -1  # cai no fluxo de restart abaixo
            else:
                if red_since is not None and status in ("green", "yellow"):
                    logger.info("Saúde voltou para '%s'.", status)
                red_since = None
            if exit_code is None:
                continue

        # Processo morreu (ou foi derrubado pela saúde red). Reinicia.
        restarts += 1
        uptime = time.monotonic() - started_at
        if uptime >= _STABLE_RESET_S:
            backoff = _BACKOFF_START_S  # era estável; não é crash-loop
        logger.warning(
            "Backend caiu (exit=%s, uptime=%.0fs). Restart #%d em %.0fs.",
            exit_code, uptime, restarts, backoff,
        )
        time.sleep(backoff)
        backoff = min(backoff * 2, _BACKOFF_CAP_S)
        red_since = None
        child = _spawn()
        started_at = time.monotonic()


if __name__ == "__main__":
    raise SystemExit(main())
