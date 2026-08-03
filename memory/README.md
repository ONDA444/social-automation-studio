# Memory — post-mortems de produção

Post-mortems reais (sintoma → causa raiz → fix → evidência) de bugs já
investigados e resolvidos em produção. Antes de investigar um sintoma
parecido a partir do zero, procure aqui — a causa raiz pode já estar
documentada.

Convenção: `AAAA-MM-DD-assunto.md`, com Symptom/Root cause/Fix/Evidence/Status.

| Data | Post-mortem | Tags | Resumo |
|---|---|---|---|
| 2026-06-30 | [drive-api-enable-and-disconnect.md](2026-06-30-drive-api-enable-and-disconnect.md) | `drive` `oauth` `config` | Sync do Drive dava `403` porque a Drive API não estava habilitada no projeto Google Cloud (não era um problema de OAuth em si). |
| 2026-06-30 | [drive-oauth-redirect-uri-mismatch.md](2026-06-30-drive-oauth-redirect-uri-mismatch.md) | `drive` `oauth` `config` | Login do Drive falhava com `redirect_uri_mismatch` por drift entre a URL de produção (Railway) e o redirect URI cadastrado no Google Cloud. |
| 2026-07-01 | [drive-clear-active-inventory.md](2026-07-01-drive-clear-active-inventory.md) | `drive` `inventory` | Trocar o canal de nicho no Drive deixava vídeos antigos "presos" como `missing` na UI; faltava endpoint para limpar o estoque de um canal. |
| 2026-07-01 | [drive-generic-child-root-regression.md](2026-07-01-drive-generic-child-root-regression.md) | `drive` `sync` | `_resolve_niche_roots()` aceitava match parcial nos dois sentidos e podia escolher uma subpasta genérica (`Videos`) como raiz do nicho, indexando só uma fração da pasta. |
| 2026-07-01 | [drive-inventory-account-switch-race.md](2026-07-01-drive-inventory-account-switch-race.md) | `drive` `frontend` `race-condition` | Trocar de canal e voltar podia mostrar `0` vídeos indexados por causa de uma race entre `setState` assíncrono e `loadDrive()` usando o nicho antigo. |
| 2026-07-01 | [drive-mode-root-cause.md](2026-07-01-drive-mode-root-cause.md) | `drive` `scheduler` `trending` | Canais em modo `drive` ainda geravam jobs de trending via `_job_ride_trends`, que ignorava `video_source_mode`; scheduler de Drive também dependia indevidamente de `ThemeQueue`. |
| 2026-07-01 | [drive-niche-switch-inventory.md](2026-07-01-drive-niche-switch-inventory.md) | `drive` `inventory` | Trocar de nicho não aposentava os `ReadyVideo` antigos da conta, misturando estoque do nicho novo com o antigo na UI. |
| 2026-07-01 | [drive-subfolder-indexing-root-cause.md](2026-07-01-drive-subfolder-indexing-root-cause.md) | `drive` `sync` | Sync indexava só um branch de subpastas, ignorava atalhos do Drive e não resolvia URLs de busca (`/drive/search?q=...`) sem folder id. |
| 2026-07-01 | [drive-subfolder-parent-climb.md](2026-07-01-drive-subfolder-parent-climb.md) | `drive` `sync` | URL salva apontando para uma subpasta filha nunca subia pelos `parents` do Drive até achar a pasta raiz real do nicho, indexando só a subpasta colada. |
| 2026-07-02 | [drive-seo-title-regression.md](2026-07-02-drive-seo-title-regression.md) | `drive` `seo` | Mudança de SEO viral forçava um template de título (`"Epic ..."`, `"olha o detalhe"`) mesmo quando a análise do vídeo já tinha um gancho natural melhor. |
| 2026-07-02 | [youtube-oauth-scope-changed.md](2026-07-02-youtube-oauth-scope-changed.md) | `youtube` `oauth` | Callback OAuth do YouTube dava `500` quando o Google devolvia um scope extra do Drive (`include_granted_scopes=true`) e `oauthlib` levantava exceção não tratada. |
| 2026-07-02 | [youtube-refresh-token-churn.md](2026-07-02-youtube-refresh-token-churn.md) | `youtube` `oauth` | `prompt=consent` forçado em todo reconnect causava rotação desnecessária de refresh tokens, e o save de credenciais podia sobrescrever um refresh token válido com `None`. |

## Como usar

- Sintoma parecido com Drive sync, OAuth ou SEO? Procure pela tag na tabela
  acima antes de reabrir a investigação do zero.
- Ao fechar um novo post-mortem, adicione uma linha aqui (data, link, tags,
  resumo de uma frase) e linke o arquivo em `README.md` se for uma decisão de
  arquitetura maior (ver seção "Docs de arquitetura").
