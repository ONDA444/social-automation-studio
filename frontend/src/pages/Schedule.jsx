import { useEffect, useState } from 'react'
import { api, API_BASE } from '../api'
import CalendarView from '../components/CalendarView.jsx'
import { PageHeader } from '../components/ui.jsx'
import { fmtDate, PLATFORM_META } from '../lib'

const BASE_API = API_BASE

const FALLBACK_CONTENT_TYPES = [
  { value: 'film_recap_ai_images', label: 'Recap (imagens IA)' },
  { value: 'sports_highlights', label: 'Esportes (highlights)' },
  { value: 'quote_viral', label: 'Frase viral' },
  { value: 'top_list_ranking', label: 'Top list / Ranking' },
  { value: 'explainer_curiosity', label: 'Curiosidade / Explainer' },
  { value: 'true_crime_mystery', label: 'True crime / Mistério' },
  { value: 'motivational_speech', label: 'Motivacional' },
  { value: 'reddit_story', label: 'Reddit Story' },
]

const parseThemes = (raw) => (raw || '').split(/[\n,]+/).map((t) => t.trim()).filter(Boolean)

const MODE_OPTIONS = [
  { v: 'fixed',          label: '🕐 Fixo',         desc: 'Você define os horários exatos.' },
  { v: 'smart',          label: '🧠 Inteligente',   desc: 'O sistema escolhe os melhores horários automaticamente.' },
  { v: 'trending_aware', label: '🔥 Trending',      desc: 'Publica logo que um trending é detectado.' },
]

export default function Schedule() {
  const [events, setEvents]     = useState([])
  const [accounts, setAccounts] = useState([])
  const [sel, setSel]           = useState('')
  const [cfg, setCfg]           = useState({
    mode: 'fixed', videos_per_day: 1, post_times: ['19:00'],
    timezone: 'America/Sao_Paulo', auto_shorts: true, shorts_formats: [1, 2, 4],
  })
  const [slots, setSlots]       = useState([])
  const [slotsMeta, setSlotsMeta] = useState(null)
  const [saving, setSaving]     = useState(false)

  const [themesText, setThemesText]   = useState('')
  const [themeType, setThemeType]     = useState('film_recap_ai_images')
  const [themeFormat, setThemeFormat] = useState('long')
  const [contentTypes, setContentTypes] = useState(FALLBACK_CONTENT_TYPES)
  const [queue, setQueue]   = useState([])
  const [history, setHistory] = useState([])   // consumed themes (already turned into videos)
  const [showHistory, setShowHistory] = useState(false)
  const [busy, setBusy]     = useState(false)

  const selAccount = accounts.find((a) => String(a.id) === sel)
  const platMeta   = selAccount ? (PLATFORM_META[selAccount.platform] || {}) : {}

  useEffect(() => {
    api.get('/schedule/calendar').then((d) => setEvents(d.events || [])).catch(() => {})
    api.get('/accounts')
      .then((d) => { const a = d.accounts || []; setAccounts(a); if (a[0]) setSel(String(a[0].id)) })
      .catch(() => {})
    api.get('/jobs/content-types')
      .then((d) => { const list = Array.isArray(d) ? d : d?.content_types; if (Array.isArray(list) && list.length) setContentTypes(list) })
      .catch(() => setContentTypes(FALLBACK_CONTENT_TYPES))
  }, [])

  useEffect(() => {
    if (!sel) return
    api.get(`/schedule/config/${sel}`)
      .then((c) => setCfg((p) => ({ ...p, ...c, post_times: c.post_times?.length ? c.post_times : p.post_times })))
      .catch(() => {})
    api.get(`/schedule/${sel}/slots?count=6`).then((d) => { setSlots(d.slots || []); setSlotsMeta(d) }).catch(() => {})
  }, [sel])

  const loadQueue = () => {
    if (!sel) { setQueue([]); setHistory([]); return }
    api.get(`/themes?account_id=${sel}&status=pending`).then((d) => setQueue(d.themes || [])).catch(() => setQueue([]))
    // Consumed themes (already generated) — shown as history so they don't look "lost".
    api.get(`/themes?account_id=${sel}&status=consumed`)
      .then((d) => setHistory((d.themes || []).slice().reverse()))
      .catch(() => setHistory([]))
  }
  useEffect(() => { loadQueue() }, [sel])

  const saveCfg = async () => {
    setSaving(true)
    try {
      await api.put(`/schedule/config/${sel}`, cfg)
      const d = await api.get(`/schedule/${sel}/slots?count=6&mode=${cfg.mode}`)
      setSlots(d.slots || [])
      setSlotsMeta(d)
    } catch (e) { alert(e.message) } finally { setSaving(false) }
  }

  const openOAuth = (acct) => {
    window.open(`${BASE_API}/auth/${acct.platform}/begin?account_id=${acct.id}`, 'oauth', 'width=500,height=640')
  }

  const parsedThemes = parseThemes(themesText)

  const addThemes = async () => {
    if (!sel) return alert('Selecione uma conta primeiro')
    if (parsedThemes.length === 0) return alert('Informe ao menos um tema')
    setBusy(true)
    try {
      const r = await api.post('/themes', {
        account_id: Number(sel), themes: parsedThemes,
        content_type: themeType, format: themeFormat, target_platforms: ['youtube'],
      })
      alert(`${r?.created ?? 0} tema(s) adicionado(s) à fila`)
      setThemesText('')
      loadQueue()
    } catch (e) { alert(e.message) } finally { setBusy(false) }
  }

  const deleteTheme = async (id) => {
    try { await api.del(`/themes/${id}`); loadQueue() } catch (e) { alert(e.message) }
  }

  // Re-queue a consumed theme as a fresh pending one (re-uses POST /themes — no
  // backend change). Lets the user regenerate a theme whose video failed/was lost.
  const regenTheme = async (t) => {
    if (!sel) return
    try {
      await api.post('/themes', {
        account_id: Number(sel), themes: [t.theme],
        content_type: t.content_type, format: t.format,
        target_platforms: t.target_platforms || ['youtube'],
      })
      loadQueue()
    } catch (e) { alert(e.message) }
  }

  const clearThemes = async () => {
    if (queue.length === 0) return
    if (!confirm(`Limpar ${queue.length} tema(s) da fila?`)) return
    try { await api.post('/themes/bulk-delete', { status: 'pending' }); loadQueue() } catch (e) { alert(e.message) }
  }

  return (
    <div className="space-y-6 fade-in">
      <PageHeader title="Agenda" sub="Horários de publicação, modo inteligente e automação por temas." />

      <div className="grid lg:grid-cols-3 gap-6">
        {/* Calendário */}
        <div className="lg:col-span-2">
          <CalendarView events={events} />
        </div>

        {/* Painel de configuração */}
        <div className="card p-5 space-y-4 h-fit">
          <h3 className="heading font-semibold text-base">Configuração de slots</h3>

          {/* Seletor de conta */}
          <div>
            <label className="text-xs text-text-muted mb-1 block">Canal</label>
            <select className="input" value={sel} onChange={(e) => setSel(e.target.value)}>
              <option value="">Selecione a conta</option>
              {accounts.map((a) => {
                const m = PLATFORM_META[a.platform] || {}
                return <option key={a.id} value={a.id}>{m.icon ? `${m.icon} ` : ''}{a.display_name}</option>
              })}
            </select>
          </div>

          {/* Status de conexão */}
          {sel && selAccount && (
            <div className={`flex items-center justify-between p-3 rounded-xl border transition-colors ${
              selAccount.has_credentials
                ? 'border-success/25 bg-success/5'
                : 'border-warning/25 bg-warning/5'
            }`}>
              <div className="flex items-center gap-2">
                <span style={{ color: platMeta.color }} className="text-base">{platMeta.icon}</span>
                <span className="text-sm font-medium">{selAccount.display_name}</span>
              </div>
              {selAccount.has_credentials ? (
                <span className="badge text-[11px]"
                  style={{ background: 'rgba(0,214,143,0.15)', color: 'var(--success)', border: '1px solid rgba(0,214,143,0.25)' }}>
                  ● Conectado
                </span>
              ) : (
                <button className="btn-ghost text-xs py-1 px-3" onClick={() => openOAuth(selAccount)}>
                  🔗 Conectar {selAccount.platform === 'youtube' ? 'YouTube' : selAccount.platform === 'tiktok' ? 'TikTok' : 'Instagram'}
                </button>
              )}
            </div>
          )}

          {sel && (
            <>
              {/* Seletor de modo */}
              <div>
                <label className="text-xs text-text-muted mb-1.5 block">Modo de publicação</label>
                <div className="grid grid-cols-3 gap-1 p-1 rounded-xl bg-elevated border border-border">
                  {MODE_OPTIONS.map((m) => (
                    <button key={m.v} type="button"
                      onClick={() => setCfg({ ...cfg, mode: m.v })}
                      className={`text-[11px] py-1.5 px-1 rounded-lg font-semibold transition-all duration-150 leading-tight ${
                        cfg.mode === m.v
                          ? 'bg-accent text-white shadow-sm'
                          : 'text-text-muted hover:text-text-primary'
                      }`}>
                      {m.label}
                    </button>
                  ))}
                </div>
                {MODE_OPTIONS.find((m) => m.v === cfg.mode) && (
                  <p className="text-[11px] text-text-muted mt-1.5">
                    {MODE_OPTIONS.find((m) => m.v === cfg.mode).desc}
                  </p>
                )}
              </div>

              {/* Vídeos por dia */}
              <div>
                <label className="text-xs text-text-muted mb-1 block">Vídeos por dia</label>
                <input type="number" min="1" max="6" className="input"
                  value={cfg.videos_per_day}
                  onChange={(e) => setCfg({ ...cfg, videos_per_day: Number(e.target.value) })} />
              </div>

              {/* Horários — só visível no modo fixo */}
              {cfg.mode === 'fixed' && (
                <div className="slide-down">
                  <label className="text-xs text-text-muted mb-1 block">Horários (vírgula)</label>
                  <input className="input font-mono"
                    value={(cfg.post_times || []).join(', ')}
                    onChange={(e) => setCfg({ ...cfg, post_times: e.target.value.split(',').map((s) => s.trim()) })}
                    placeholder="19:00, 21:00" />
                  <p className="text-[11px] text-text-muted mt-1">Ex: 10:00, 14:30, 19:00</p>
                </div>
              )}

              {/* Gerar Shorts automático */}
              <div className="p-3 rounded-xl border border-border bg-elevated/40">
                <label className="flex items-center gap-2.5 text-sm cursor-pointer select-none">
                  <input type="checkbox" className="accent-accent w-4 h-4 shrink-0"
                    checked={cfg.auto_shorts}
                    onChange={(e) => setCfg({ ...cfg, auto_shorts: e.target.checked })} />
                  <span className="font-medium">Gerar Shorts automaticamente</span>
                </label>
                <p className="text-[11px] text-text-muted mt-1.5 ml-[26px]">
                  Cada vídeo longo também gera uma versão vertical 9:16 (Shorts/Reels) no mesmo slot — sem precisar criar um tema separado.
                </p>
              </div>

              <button className="btn-primary w-full" onClick={saveCfg} disabled={saving}>
                {saving ? 'Salvando…' : 'Salvar configuração'}
              </button>

              {/* Próximos slots */}
              {slots.length > 0 && (
                <div className="pt-3 border-t border-border space-y-2">
                  {/* Smart mode feedback */}
                  {cfg.mode === 'smart' && slotsMeta && (
                    <div className="rounded-lg p-2.5 text-[11px]"
                      style={{ background: slotsMeta.from_analytics ? 'rgba(0,245,160,0.06)' : 'rgba(124,106,255,0.06)',
                               border: `1px solid ${slotsMeta.from_analytics ? 'rgba(0,245,160,0.2)' : 'rgba(124,106,255,0.2)'}` }}>
                      {slotsMeta.from_analytics ? (
                        <p style={{ color: 'var(--success)' }}>
                          ✓ Horários aprendidos com seus dados: <b>{(slotsMeta.effective_times || []).join(', ')}</b>
                        </p>
                      ) : (
                        <>
                          <p style={{ color: 'var(--accent)' }}>
                            🧠 Usando melhores horários padrão: <b>{(slotsMeta.effective_times || []).join(', ')}</b>
                          </p>
                          <p className="text-text-muted mt-0.5">Os horários vão se adaptar automaticamente conforme seu canal crescer.</p>
                        </>
                      )}
                    </div>
                  )}

                  <p className="text-xs text-text-muted">Próximos slots agendados:</p>
                  <ul className="space-y-1.5">
                    {slots.map((s, i) => (
                      <li key={i} className="flex items-center gap-2 text-[12px]">
                        <span className="text-accent">▸</span>
                        <span className="font-mono text-text-primary">{fmtDate(s)}</span>
                      </li>
                    ))}
                  </ul>

                  {/* Queue size hint */}
                  {queue.length > 0 && (
                    <p className="text-[11px] text-text-muted pt-1 border-t border-border">
                      {queue.length} tema{queue.length !== 1 ? 's' : ''} na fila · geração automática a cada slot
                    </p>
                  )}
                </div>
              )}
            </>
          )}
        </div>
      </div>

      {/* ---- Automação por temas ---- */}
      <div className="card p-5 space-y-5">
        <div>
          <h3 className="heading font-semibold">Automação por temas</h3>
          <p className="text-xs text-text-muted mt-1">
            O sistema gera 1 vídeo por tema nos horários da cadência acima. Cada vídeo vai para Aprovações antes de publicar (ou publica direto com AUTO_PUBLISH ativo).
          </p>
        </div>

        {!sel ? (
          <p className="text-sm text-text-muted text-center py-6">
            Selecione uma conta acima para configurar a fila de temas.
          </p>
        ) : (
          <div className="grid lg:grid-cols-2 gap-6">
            {/* Coluna: adicionar temas */}
            <div className="space-y-3">
              <div>
                <label className="text-xs text-text-muted mb-1 block">Temas</label>
                <textarea
                  className="input min-h-[130px] resize-y"
                  rows={5}
                  value={themesText}
                  onChange={(e) => setThemesText(e.target.value)}
                  placeholder={'Um tema por linha (ou separados por vírgula):\nCopa 1958\nCopa 1970\nPelé vs Maradona'}
                />
                <p className="text-[11px] text-text-muted mt-1">
                  {parsedThemes.length} {parsedThemes.length === 1 ? 'tema detectado' : 'temas detectados'}
                </p>
              </div>

              <div>
                <label className="text-xs text-text-muted mb-1 block">Tipo de conteúdo</label>
                <select className="input" value={themeType} onChange={(e) => setThemeType(e.target.value)}>
                  {contentTypes.map((c) => <option key={c.value} value={c.value}>{c.label}</option>)}
                </select>
              </div>

              <div>
                <label className="text-xs text-text-muted mb-1 block">Formato</label>
                <div className="grid grid-cols-2 gap-2">
                  {[
                    { v: 'long',  label: '🖥️ Vídeo longo', hint: '16:9 · YouTube' },
                    { v: 'short', label: '📱 Shorts',       hint: '9:16 · TikTok/Reels' },
                  ].map((f) => (
                    <button key={f.v} type="button" onClick={() => setThemeFormat(f.v)}
                      className={`rounded-xl border p-3 text-left text-xs transition-all duration-150 ${
                        themeFormat === f.v
                          ? 'border-accent bg-accent/10 text-text-primary'
                          : 'border-border text-text-muted hover:bg-elevated'
                      }`}>
                      <div className="font-semibold">{f.label}</div>
                      <div className="text-[10px] opacity-70 mt-0.5">{f.hint}</div>
                    </button>
                  ))}
                </div>
              </div>

              <button
                className="btn-primary w-full"
                disabled={busy || parsedThemes.length === 0}
                onClick={addThemes}>
                {busy
                  ? 'Adicionando...'
                  : `Adicionar ${parsedThemes.length > 0 ? parsedThemes.length + ' tema(s)' : ''} à fila`}
              </button>
            </div>

            {/* Coluna: fila pendente */}
            <div className="space-y-2">
              <div className="flex items-center justify-between">
                <p className="text-sm font-medium">
                  Fila de temas{' '}
                  <span className="badge ml-1"
                    style={{ background: 'rgba(108,92,231,0.15)', color: 'var(--accent)', border: '1px solid rgba(108,92,231,0.25)' }}>
                    {queue.length}
                  </span>
                </p>
                {queue.length > 0 && (
                  <button className="btn-ghost text-xs" onClick={clearThemes}>🧹 Limpar todos</button>
                )}
              </div>

              {queue.length === 0 ? (
                <div className="card p-8 text-center text-text-muted border-dashed">
                  <p className="text-3xl mb-2">📋</p>
                  <p className="text-sm">Nenhum tema na fila.</p>
                  <p className="text-xs mt-1 opacity-70">Adicione temas ao lado para começar.</p>
                </div>
              ) : (
                <ul className="space-y-2 max-h-[340px] overflow-y-auto pr-1">
                  {queue.map((t) => (
                    <li key={t.id} className="card p-3 flex items-center gap-2 card-hover">
                      <div className="flex-1 min-w-0">
                        <p className="text-sm font-medium truncate" title={t.theme}>{t.theme}</p>
                        <p className="text-[11px] text-text-muted mt-0.5">
                          {t.content_type} · {t.format === 'short' ? '📱 Short' : '🖥️ Longo'}
                        </p>
                      </div>
                      <span className="badge shrink-0"
                        style={{ background: 'rgba(255,182,39,0.12)', color: 'var(--warning)', border: '1px solid rgba(255,182,39,0.2)' }}>
                        {t.status}
                      </span>
                      <button className="btn-ghost text-xs p-1.5 shrink-0" onClick={() => deleteTheme(t.id)} title="Excluir tema">
                        🗑
                      </button>
                    </li>
                  ))}
                </ul>
              )}

              {/* History: consumed themes (already generated) — proves nothing was lost */}
              {history.length > 0 && (
                <div className="pt-1">
                  <button type="button" onClick={() => setShowHistory((s) => !s)}
                    className="text-xs text-text-muted hover:text-text-primary flex items-center gap-1">
                    {showHistory ? '▾' : '▸'} Já gerados ({history.length})
                  </button>
                  {showHistory && (
                    <ul className="space-y-2 max-h-[300px] overflow-y-auto pr-1 mt-2">
                      {history.map((t) => (
                        <li key={t.id} className="card p-3 flex items-center gap-2 opacity-70">
                          <div className="flex-1 min-w-0">
                            <p className="text-sm truncate" title={t.theme}>{t.theme}</p>
                            <p className="text-[11px] text-text-muted mt-0.5">
                              {t.content_type} · {t.format === 'short' ? '📱 Short' : '🖥️ Longo'} · já gerado
                            </p>
                          </div>
                          <button className="btn-ghost text-xs shrink-0" onClick={() => regenTheme(t)}
                            title="Gerar este tema de novo (vira um novo vídeo)">
                            ↻ Gerar de novo
                          </button>
                        </li>
                      ))}
                    </ul>
                  )}
                </div>
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
