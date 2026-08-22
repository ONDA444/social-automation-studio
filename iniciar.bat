@echo off
setlocal enabledelayedexpansion
title Social Automation Studio
echo ============================================
echo   Social Automation Studio - Iniciando...
echo ============================================

cd /d "%~dp0"

REM --- Checagens rapidas ---
where ffmpeg >nul 2>&1 || (echo [ERRO] FFmpeg nao encontrado no PATH. && pause && exit /b 1)

REM --- 1a vez: cria venv e instala tudo. Depois, pula para iniciar rapido. ---
if not exist .venv\Scripts\uvicorn.exe (
  echo [..] Primeira execucao: instalando dependencias ^(pode levar alguns minutos^)...
  if not exist .venv python -m venv .venv
  call .venv\Scripts\activate.bat
  python -m pip install --upgrade pip -q
  pip install -r backend\requirements.txt -q
  if not exist .env copy .env.example .env >nul
  python -m backend.database --init
) else (
  call .venv\Scripts\activate.bat
)

if not exist frontend\node_modules (
  echo [..] Instalando dependencias do frontend...
  pushd frontend && call npm install && popd
)

REM --- Sobe backend e frontend em janelas separadas ---
echo [..] Iniciando backend  (http://localhost:8000) com watchdog anti-queda...
start "Backend - Social Studio" cmd /k ".venv\Scripts\activate.bat && python -m backend.watchdog"

echo [..] Iniciando frontend (http://localhost:5173)...
start "Frontend - Social Studio" cmd /k "cd frontend && npm run dev"

REM --- Espera os servidores subirem e abre o Chrome no dashboard ---
echo [..] Aguardando servidores...
timeout /t 8 /nobreak >nul

set "CHROME="
if exist "%ProgramFiles%\Google\Chrome\Application\chrome.exe" set "CHROME=%ProgramFiles%\Google\Chrome\Application\chrome.exe"
if exist "%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe" set "CHROME=%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"
if exist "%LocalAppData%\Google\Chrome\Application\chrome.exe" set "CHROME=%LocalAppData%\Google\Chrome\Application\chrome.exe"

if defined CHROME (
  echo [OK] Abrindo no Chrome...
  start "" "!CHROME!" "http://localhost:5173"
) else (
  echo [INFO] Chrome nao encontrado - abrindo no navegador padrao.
  start "" "http://localhost:5173"
)

echo.
echo ============================================
echo   PRONTO!
echo   Dashboard: http://localhost:5173
echo   API Docs:  http://localhost:8000/docs
echo.
echo   Para PARAR: feche as janelas "Backend" e "Frontend".
echo ============================================
echo Esta janela pode ser fechada.
timeout /t 6 /nobreak >nul
