#!/usr/bin/env bash
set -e

echo "═══════════════════════════════════════"
echo "   Social Automation Studio — Startup  "
echo "═══════════════════════════════════════"

# 1. System deps
echo "▶ Verificando dependências..."
command -v python3 >/dev/null || { echo "❌ Python3 não encontrado"; exit 1; }
command -v ffmpeg  >/dev/null || { echo "❌ FFmpeg não encontrado (sudo apt install ffmpeg)"; exit 1; }
command -v node    >/dev/null || { echo "❌ Node.js não encontrado"; exit 1; }
echo "✓ Dependências OK"

# 2. .env
if [ ! -f .env ]; then cp .env.example .env; echo "⚠ .env criado — preencha as chaves de API."; fi

# 3. venv + Python deps
if [ ! -d .venv ]; then python3 -m venv .venv; fi
# shellcheck disable=SC1091
source .venv/bin/activate
echo "▶ Instalando dependências Python..."
pip install --upgrade pip -q
pip install -r backend/requirements.txt -q

# 4. Node deps
echo "▶ Instalando dependências Node..."
(cd frontend && npm install --silent)

# 5. Dirs + DB
mkdir -p tmp output cache/broll cache/thumbnails cache/remix_refs assets/music logs
echo "▶ Inicializando banco..."
python -m backend.database --init

# 6. Redis/Celery — optional. With Docker, start them; else in-process mode.
USE_CELERY=0
if command -v docker >/dev/null && docker compose up -d redis >/dev/null 2>&1; then
  echo "✓ Redis iniciado via Docker"
  USE_CELERY=1
else
  echo "ℹ Docker/Redis ausente — rodando em modo in-process (funcional para uso local)."
fi

# 7. Health check
[ -f health_check.sh ] && bash health_check.sh || true

PIDS=()
cleanup() { echo; echo "Parando..."; for p in "${PIDS[@]}"; do kill "$p" 2>/dev/null || true; done; exit 0; }
trap cleanup INT TERM

if [ "$USE_CELERY" = "1" ]; then
  celery -A backend.pipeline.celery_app worker --loglevel=warning --concurrency=2 & PIDS+=($!)
  celery -A backend.pipeline.celery_app beat --loglevel=warning & PIDS+=($!)
fi

echo "▶ Iniciando backend..."
uvicorn backend.main:app --host 0.0.0.0 --port 8000 --reload & PIDS+=($!)
sleep 3
echo "▶ Iniciando frontend..."
(cd frontend && npm run dev) & PIDS+=($!)
sleep 3

echo ""
echo "═══════════════════════════════════════"
echo "✓ SISTEMA INICIADO"
echo "  Dashboard: http://localhost:5173"
echo "  API Docs:  http://localhost:8000/docs"
echo "  Ctrl+C para parar."
echo "═══════════════════════════════════════"
wait
