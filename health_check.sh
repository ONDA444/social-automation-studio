#!/usr/bin/env bash
echo "▶ Health Check..."
ERRORS=0
# Prefer the project venv (Linux/Mac then Windows path), else system python.
PY=".venv/bin/python"
[ -x "$PY" ] || PY=".venv/Scripts/python.exe"
[ -x "$PY" ] || PY="python3"

# FFmpeg
ffmpeg -version >/dev/null 2>&1 && echo "  ✓ FFmpeg: OK" || { echo "  ✗ FFmpeg: FALHOU"; ERRORS=$((ERRORS+1)); }

# edge-tts
$PY -c "import edge_tts" 2>/dev/null && echo "  ✓ edge-tts: OK" || echo "  ⚠ edge-tts: não instalado (pip install edge-tts)"

# Redis (optional)
if command -v redis-cli >/dev/null; then
  redis-cli -u "${REDIS_URL:-redis://localhost:6379}" ping >/dev/null 2>&1 \
    && echo "  ✓ Redis: OK" || echo "  ⚠ Redis: indisponível (modo in-process)"
else
  echo "  ⚠ Redis: redis-cli ausente (modo in-process)"
fi

# Groq (if key set)
if [ -n "$GROQ_API_KEY" ]; then
  echo "  ✓ Groq: chave configurada"
else
  echo "  ⚠ Groq: sem chave (usando roteiro offline)"
fi

# Pollinations / image providers
if [ -n "$POLLINATIONS_TOKEN" ] || [ -n "$HUGGINGFACE_TOKEN" ] || [ -n "$PEXELS_API_KEY" ]; then
  echo "  ✓ Imagens: provedor configurado"
else
  echo "  ⚠ Imagens: sem chave (usando placeholders locais)"
fi

[ $ERRORS -eq 0 ] && echo "  ✓ Sistema saudável" || echo "  ⚠ $ERRORS problema(s) crítico(s)"
