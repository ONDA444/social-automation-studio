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

Cada agente baseado em `BaseAgent` tem retry/backoff e emite eventos WebSocket
(AgentLog ao vivo). A maioria tem um demo standalone via
`python -m backend.agents.<nome>` — olhe o bloco `if __name__ == "__main__":`
de cada um antes de rodar: nem todos aceitam literalmente `--test` (alguns
tomam argumento posicional, ex. `analyzer.py <url>`). Os módulos que são
serviços sobre o banco (AccountProfile, Analytics, Drive Library, Performance)
ou funções utilitárias sem estado de agente (Agenda, ChannelOptimizer,
ChannelRefresh, CrossPlatformLinker, KeywordResearch, ManualUpload,
QualityGate, ReadyVideoCuration, StyleGuide) não têm demo isolado — são
cobertos pelos testes em `backend/tests/`.

Efeitos FFmpeg testáveis: `python -m backend.effects.ffmpeg_effects --list`.

CLI de operador (agenda/refresh de um canal direto do terminal, sem dashboard):

```bash
python -m backend.cli agenda --channel 3                    # ver a agenda do dia
python -m backend.cli agenda --channel 3 --generate          # gerar jobs até fechar os limites diários
python -m backend.cli refresh --channel 3                    # reprocessar curadoria/design do canal
python -m backend.cli refresh --channel 3 --job 4821          # reprocessar só um job
```

---

## ☁️ Deploy: GitHub → Railway (backend) + Vercel (frontend)

### Passo 1 — Criar repositório no GitHub

1. Acesse **github.com/new**
2. Nome: `social-automation-studio` — visibilidade **Private**
3. **NÃO** marque "Add a README" (o repo deve começar vazio)
4. Clique em **Create repository**

### Passo 2 — Subir o código

No terminal, dentro desta pasta:

```bash
git remote add origin https://github.com/SEU_USUARIO/social-automation-studio.git
git push -u origin main
```

> Substitua `SEU_USUARIO` pelo seu usuário do GitHub.

---

### Passo 3 — Railway (backend)

1. Acesse **railway.app** → **New Project → Deploy from GitHub repo**
2. Selecione `social-automation-studio` — o Railway detecta `nixpacks.toml` (FFmpeg, Python 3.11) e `railway.toml` automaticamente
3. Clique em **Deploy**
4. Na aba **+ New**, adicione os plugins: **PostgreSQL** e **Redis** (injetam `DATABASE_URL` e `REDIS_URL` automaticamente)
5. Vá em **Variables** e adicione:

| Variável | Obrigatório | Valor |
|---|---|---|
| `GROQ_API_KEY` | ✅ | console.groq.com |
| `GEMINI_API_KEY` | ✅ | aistudio.google.com |
| `PEXELS_API_KEY` | ✅ | pexels.com/api |
| `SECRET_KEY` | ✅ | string aleatória (ex: `python -c "import secrets; print(secrets.token_hex(32))"`) |
| `LMNT_API_KEY` | Narração premium | app.lmnt.com |
| `LMNT_VOICE` | Narração premium | ID da voz no LMNT |
| `TTS_PROVIDER` | — | `auto` |
| `PIXABAY_API_KEY` | — | pixabay.com/api |
| `POLLINATIONS_TOKEN` | — | enter.pollinations.ai |
| `CORS_ORIGINS` | Após Vercel | `https://SEU-PROJETO.vercel.app` |

**OAuth (publicação automática — opcional):**

```
GOOGLE_CLIENT_ID=...
GOOGLE_CLIENT_SECRET=...
GOOGLE_REDIRECT_URI=https://SEU-BACKEND.railway.app/auth/youtube/callback

TIKTOK_CLIENT_KEY=...
TIKTOK_CLIENT_SECRET=...
TIKTOK_REDIRECT_URI=https://SEU-BACKEND.railway.app/auth/tiktok/callback

META_APP_ID=...
META_APP_SECRET=...
META_REDIRECT_URI=https://SEU-BACKEND.railway.app/auth/instagram/callback
```

6. Aguarde o deploy concluir — copie a URL pública (ex: `https://social-automation-studio-production.up.railway.app`)

---

### Passo 4 — Vercel (frontend)

1. Acesse **vercel.com/new** → **Import Git Repository**
2. Selecione `social-automation-studio` — o Vercel detecta `vercel.json` automaticamente
3. Em **Environment Variables**, adicione:

| Variável | Valor |
|---|---|
| `VITE_API_URL` | `https://SEU-BACKEND.railway.app/api` |

> Use a URL copiada no Passo 3.6, com `/api` no final.

> ⚠️ **`VITE_API_URL` é obrigatório neste passo.** Se a variável ficar em
> branco, `frontend/src/api.js` cai num fallback hardcoded (`RAILWAY_BACKEND`)
> que aponta para o backend Railway do projeto original — **não** para o seu.
> Ao criar um projeto Railway novo (outro slug/domínio), ou se o backend for
> redeployado com outra URL, defina `VITE_API_URL` aqui; não dependa do
> fallback.

4. Clique em **Deploy**
5. Copie a URL do Vercel (ex: `https://social-automation-studio.vercel.app`)
6. Volte ao Railway → **Variables** → adicione `CORS_ORIGINS=https://social-automation-studio.vercel.app`

---

**Pronto.** O dashboard fica no Vercel, a API no Railway.
Cada `git push origin main` faz redeploy automático nos dois serviços.

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

## 📚 Docs de arquitetura

Decisões de design (o "porquê" por trás do código) ficam em `docs/`:

| Doc | Conteúdo |
|---|---|
| [`docs/superpowers/specs/2026-06-30-drive-ready-videos-design.md`](docs/superpowers/specs/2026-06-30-drive-ready-videos-design.md) | Segunda fonte de conteúdo (vídeos prontos do Google Drive) e os Channel Modes `ai`/`drive`/`mixed` |
| [`docs/superpowers/specs/2026-06-30-drive-video-analysis-seo-design.md`](docs/superpowers/specs/2026-06-30-drive-video-analysis-seo-design.md) | Análise + geração de metadados/SEO reais para vídeos do Drive (em vez de título genérico) |
| [`docs/superpowers/specs/2026-07-01-drive-monetization-smart-schedule-design.md`](docs/superpowers/specs/2026-07-01-drive-monetization-smart-schedule-design.md) | Monetização/localização nos vídeos do Drive e o agendamento inteligente (smart schedule) |
| [`docs/superpowers/specs/2026-07-11-manual-video-upload-design.md`](docs/superpowers/specs/2026-07-11-manual-video-upload-design.md) | Upload manual de vídeo (arquivo do PC) como job avulso, fora da rotação do Drive |
| [`docs/MANUAL_DE_QUALIDADE.md`](docs/MANUAL_DE_QUALIDADE.md) | Manual de roteiro/gancho/retenção/SEO usado por `backend/agents/style_guide.py` |
| [`docs/SISTEMA_DE_PROMPTS_v2.md`](docs/SISTEMA_DE_PROMPTS_v2.md) | Mapa completo dos 14 estágios do pipeline e os prompts de cada agente |

Bugs de produção já investigados (sintoma → causa raiz → fix), principalmente
Drive sync e OAuth, ficam indexados em
[`memory/README.md`](memory/README.md) — confira ali antes de investigar um
sintoma parecido do zero.

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
