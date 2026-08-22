@echo off
setlocal enabledelayedexpansion
echo ============================================
echo   Social Automation Studio - Startup (Win)
echo ============================================

where ffmpeg >nul 2>&1 || (echo [ERRO] FFmpeg nao encontrado. Baixe em https://ffmpeg.org/download.html && pause && exit /b 1)
where python >nul 2>&1 || (echo [ERRO] Python nao encontrado. Instale Python 3.11+ && pause && exit /b 1)
where node >nul 2>&1 || (echo [ERRO] Node.js nao encontrado. Instale Node 18+ && pause && exit /b 1)
echo [OK] Dependencias de sistema encontradas.

if not exist .venv (
  echo [..] Criando ambiente virtual Python...
  python -m venv .venv
)
call .venv\Scripts\activate.bat

echo [..] Instalando dependencias Python (pode levar alguns minutos na 1a vez)...
python -m pip install --upgrade pip -q
pip install -r backend\requirements.txt -q

if not exist .env (
  copy .env.example .env >nul
  echo [AVISO] .env criado a partir do .env.example - preencha as chaves de API.
)

echo [..] Instalando dependencias do frontend...
pushd frontend
call npm install
popd

echo [..] Inicializando banco de dados...
python -m backend.database --init

REM Redis e opcional. Com Docker, inicia Redis + Celery. Sem Docker, modo in-process.
where docker >nul 2>&1
if !errorlevel! == 0 (
  docker compose up -d redis 2>nul && echo [OK] Redis iniciado via Docker.
  start "Celery Worker" cmd /k ".venv\Scripts\activate.bat && celery -A backend.pipeline.celery_app worker --loglevel=warning --concurrency=2 --pool=solo"
) else (
  echo [INFO] Docker ausente - rodando SEM Redis/Celery ^(modo in-process^). Funcional para uso local.
)

echo [..] Iniciando backend (http://localhost:8000) com watchdog anti-queda...
start "Backend API" cmd /k ".venv\Scripts\activate.bat && python -m backend.watchdog"
timeout /t 4 /nobreak >nul

echo [..] Iniciando frontend (http://localhost:5173)...
start "Frontend" cmd /k "cd frontend && npm run dev"
timeout /t 3 /nobreak >nul

echo.
echo ============================================
echo   SISTEMA INICIADO
echo   Dashboard: http://localhost:5173
echo   API Docs:  http://localhost:8000/docs
echo ============================================
echo Feche as janelas "Backend API" e "Frontend" para parar.
pause
