# 🎬 Social Automation Studio

Gera vídeos originais por IA (roteiro → narração → visuais → edição → shorts → SEO)
e publica no **YouTube, TikTok e Instagram** usando **apenas APIs oficiais**, com
uma etapa de **aprovação humana** antes de cada publicação.

> **Princípios:** conteúdo 100% original · APIs oficiais aprovadas · revisão humana
> obrigatória · respeito a quotas (sem criar projetos extras para burlá-las).

---

## ✅ O que funciona sem nenhuma chave de API

O sistema foi construído para **degradar com elegância**. Rodando local, sem
configurar nada, você já consegue gerar um vídeo completo:

| Etapa | Sem chave | Com chave (melhor) |
|---|---|---|
| Roteiro | Template offline | Groq Llama 3.3 / Gemini |
| Narração | ✅ edge-tts (grátis, real) | — |
| Imagens | Placeholder local | Pollinations / HuggingFace FLUX / Pexels |
| Edição (FFmpeg) | ✅ completa | — |
| Música | ✅ 20 trilhas CC0 incluídas | — |
| Legendas / Shorts / SEO | ✅ completos | LLM melhora o SEO |
| Publicação | ⏸ requer credenciais OAuth | YouTube / TikTok* / Instagram |

\* **TikTok** exige aprovação da *Content Posting API* (3–5 dias úteis em
developers.tiktok.com). Sem aprovação, os jobs ficam em `tiktok_pending_approval`
— **nenhum bypass/automação de browser é usado**.

---

## 🚀 Início rápido (local)

**Pré-requisitos:** Python 3.11+, Node 18+, FFmpeg. (Docker é **opcional** — sem ele,
o sistema roda em *modo in-process* sem Redis/Celery.)

### Windows
```bat
start.bat
```

### Linux / Mac
```bash
chmod +x start.sh && ./start.sh
```

Depois abra **http://localhost:5173** (dashboard) — a API fica em
**http://localhost:8000/docs**.

> A primeira execução cria o `.venv`, instala dependências (alguns minutos),
> cria o `.env`, inicializa o banco SQLite e sobe backend + frontend.

### Manual (passo a passo)
```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r backend/requirements.txt
cp .env.example .env                                 # preencha as chaves desejadas
python -m backend.database --init
uvicorn backend.main:app --reload                    # backend :8000
# noutro terminal:
cd frontend && npm install && npm run dev            # frontend :5173
```

---

## 🔑 Chaves de API (todas têm tier gratuito)

Edite o `.env` e reinicie o backend. Configure o que quiser — nada é obrigatório
para gerar vídeos localmente.

| Variável | Para quê | Onde obter |
|---|---|---|
| `GROQ_API_KEY` | Roteiros e SEO (Llama 3.3 70B) | console.groq.com |
| `GEMINI_API_KEY` | Fallback de LLM | aistudio.google.com |
| `POLLINATIONS_TOKEN` | Imagens IA | enter.pollinations.ai |
| `HUGGINGFACE_TOKEN` | Imagens IA (FLUX.1-schnell) | huggingface.co/settings/tokens |
| `PEXELS_API_KEY` | Stock footage/fotos | pexels.com/api |
| `GOOGLE_CLIENT_ID` / `_SECRET` | Publicar no YouTube | console.cloud.google.com |
| `TIKTOK_CLIENT_KEY` / `_SECRET` | Publicar no TikTok (após aprovação) | developers.tiktok.com |
| `META_APP_ID` / `_SECRET` | Publicar Reels no Instagram | developers.facebook.com |

> ⚠️ **Pollinations** passou a exigir um token gratuito (antes era sem chave).
> Configure `POLLINATIONS_TOKEN` **ou** `HUGGINGFACE_TOKEN` para imagens reais;
> sem nenhum, o sistema usa fundos placeholder.

Conectar contas para publicar: vá em **Plataformas → Conectar conta** (fluxo OAuth).

---

## 🧠 Os 20 agentes (pipeline)

```
Scriptwriter → Narrator → Visuals → EditingDirector → MusicCurator
→ CaptionAgent → VideoEditor → ShortsFactory → SEOAgent
→ QualityControl → ComplianceAgent → [APROVAÇÃO HUMANA] → Publisher
```
Mais: **Analyzer** (Remix/StyleDNA), **AccountProfile**, **CrossPlatformLinker**,
**Trending**, **ContentCalendar**, **Analytics**, **ErrorRecovery**, **Orchestrator**.

Cada agente tem retry/backoff, emite eventos WebSocket (AgentLog ao vivo) e pode
ser testado isolado: `python -m backend.agents.<nome> --test`.

Efeitos FFmpeg testáveis: `python -m backend.effects.ffmpeg_effects --list`.

---

## ☁️ Deploy: Frontend (Vercel) + Backend (Railway)

### Railway (backend + worker + Redis + Postgres)
1. New Project → Deploy from GitHub → este repo (detecta `nixpacks.toml`, instala FFmpeg).
2. Add Plugin → **Redis** e **PostgreSQL** (injetam `REDIS_URL` / `DATABASE_URL`).
3. Em *Variables*: cole as chaves do `.env`.
4. Crie serviços extras (mesmo repo) para o worker e o beat — comandos no `Procfile`.
5. Backend fica em `https://seu-app.up.railway.app`.

### Vercel (frontend)
1. New Project → Import GitHub → este repo (usa `vercel.json`).
2. Env var **`VITE_API_URL`** = URL do backend Railway.
3. Deploy → `https://seu-projeto.vercel.app`.

Armazenamento de mídia em produção: use **Cloudflare R2** (10 GB grátis) ou
*Railway Volumes* para persistir `output/`.

---

## 🗂️ Estrutura

```
backend/    FastAPI + 20 agentes + uploaders + pipeline + routers
frontend/   React + Vite + Tailwind (8 páginas, AgentLog WebSocket)
assets/     20 trilhas CC0 + catálogo
cache/ output/ tmp/   artefatos gerados
```

---

## 🛠️ Troubleshooting

- **"FFmpeg não encontrado"** → instale e adicione ao PATH (ffmpeg.org).
- **Backend offline no dashboard** → suba `uvicorn backend.main:app --reload` em :8000.
- **Imagens são gradientes** → configure `POLLINATIONS_TOKEN` ou `HUGGINGFACE_TOKEN`.
- **Vídeo com texto duplicado** → idem (placeholders não têm imagem real).
- **Redis indisponível** → normal sem Docker; o sistema usa modo in-process.
- **QC `qc_failed_duration`** → o vídeo é construído a partir da narração; roteiros
  muito curtos geram vídeos curtos (esperado).
- **TikTok não publica** → requer aprovação oficial da Content Posting API.

---

## 📜 Licença e conformidade
Conteúdo gerado é original (roteiro IA, voz TTS, imagens IA, stock licenciado,
música CC0). **Nunca** usa footage de filmes/séries. Publicação só via APIs
oficiais, respeitando quotas e revisão humana.
