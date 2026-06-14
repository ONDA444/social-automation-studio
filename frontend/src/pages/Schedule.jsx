import { useEffect, useState } from 'react'
import { api } from '../api'
import CalendarView from '../components/CalendarView.jsx'
import { fmtDate } from '../lib'

// Fallback usado quando GET /jobs/content-types falhar ou ainda não existir.
const FALLBACK_CONTENT_TYPES = [
  { value: 'film_recap_ai_images', label: 'Recap (imagens IA)' },
  { value: 'sports_highlights', label: 'Esportes (highlights)' },
  { value: 'quote_viral', label: 'Frase viral' },
]

// Split por quebra-de-linha E por vírgula, trim, remove vazios.
const parseThemes = (raw) => (raw || '').split(/[\n,]+/).map((t) => t.trim()).filter(Boolean)

export default function Schedule() {
  const [events, setEvents] = useState([])
  const [accounts, setAccounts] = useState([])
  const [sel, setSel] = useState('')
  const [cfg, setCfg] = useState({ mode: 'fixed', videos_per_day: 1, post_times: ['19:00'], timezone: 'America/Sao_Paulo', auto_shorts: true, shorts_formats: [1, 2, 4] })
  const [slots, setSlots] = useState([])

  // ---- Automação por temas ----
  const [themesText, setThemesText] = useState('')
  const [themeType, setThemeType] = useState('film_recap_ai_images')
  const [contentTypes, setContentTypes] = useState(FALLBACK_CONTENT_TYPES)
  const [queue, setQueue] = useState([])
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    api.get('/schedule/calendar').then((d) => setEvents(d.events)).catch(() => {})
    api.get('/accounts').then((d) => { setAccounts(d.accounts); if (d.accounts[0]) setSel(String(d.accounts[0].id)) }).catch(() => {})
    api.get('/jobs/content-types')
      .then((d) => { const list = Array.isArray(d) ? d : d?.content_types; if (Array.isArray(list) && list.length) setContentTypes(list) })
      .catch(() => setContentTypes(FALLBACK_CONTENT_TYPES))
  }, [])

  useEffect(() => {
    if (!sel) return
    api.get(`/schedule/config/${sel}`).then((c) => setCfg((p) => ({ ...p, ...c, post_times: c.post_times?.length ? c.post_times : p.post_times }))).catch(() => {})
    api.get(`/schedule/${sel}/slots?count=6`).then((d) => setSlots(d.slots)).catch(() => {})
  }, [sel])

  const loadQueue = () => {
    if (!sel) { setQueue([]); return }
    api.get(`/themes?account_id=${sel}&status=pending`).then((d) => setQueue(d.themes || [])).catch(() => setQueue([]))
  }
  useEffect(() => { loadQueue() }, [sel])

  const saveCfg = async () => {
    await api.put(`/schedule/config/${sel}`, cfg)
    const d = await api.get(`/schedule/${sel}/slots?count=6&mode=${cfg.mode}`); setSlots(d.slots)
    alert('Configuração salva')
  }

  const parsedThemes = parseThemes(themesText)

  const addThemes = async () => {
    if (!sel) return alert('Selecione uma conta primeiro')
    if (parsedThemes.length === 0) return alert('Informe ao menos um tema')
    setBusy(true)
    try {
      const r = await api.post('/themes', { account_id: Number(sel), themes: parsedThemes, content_type: themeType, target_platforms: ['youtube'] })
      alert(`${r?.created ?? 0} tema(s) adicionado(s) à fila`)
      setThemesText('')
      loadQueue()
    } catch (e) { alert(e.message) } finally { setBusy(false) }
  }

  const deleteTheme = async (id) => {
    try { await api.del(`/themes/${id}`); loadQueue() } catch (e) { alert(e.message) }
  }

  const clearThemes = async () => {
    if (queue.length === 0) return
    if (!confirm(`Limpar ${queue.length} tema(s) da fila?`)) return
    try { await api.post('/themes/bulk-delete', { status: 'pending' }); loadQueue() } catch (e) { alert(e.message) }
  }

  return (
    <div className="space-y-6">
      <h2 className="heading text-2xl font-semibold">Agenda</h2>
      <div className="grid lg:grid-cols-3 gap-6">
        <div className="lg:col-span-2"><CalendarView events={events} /></div>
        <div className="card p-5 space-y-3 h-fit">
          <h3 className="heading font-semibold">Configuração de slots</h3>
          <select className="input" value={sel} onChange={(e) => setSel(e.target.value)}>
            <option value="">Selecione a conta</option>
            {accounts.map((a) => <option key={a.id} value={a.id}>{a.display_name} ({a.platform})</option>)}
          </select>
          {sel && (
            <>
              <div>
                <label className="text-xs text-text-muted">Modo</label>
                <select className="input mt-1" value={cfg.mode} onChange={(e) => setCfg({ ...cfg, mode: e.target.value })}>
                  <option value="fixed">Fixo (horários definidos)</option>
                  <option value="smart">Inteligente (melhores horários)</option>
                  <option value="trending_aware">Trending (publicar logo)</option>
                </select>
              </div>
              <div><label className="text-xs text-text-muted">Vídeos por dia</label><input type="number" min="1" max="6" className="input mt-1" value={cfg.videos_per_day} onChange={(e) => setCfg({ ...cfg, videos_per_day: Number(e.target.value) })} /></div>
              <div><label className="text-xs text-text-muted">Horários (vírgula)</label><input className="input mt-1" value={(cfg.post_times || []).join(', ')} onChange={(e) => setCfg({ ...cfg, post_times: e.target.value.split(',').map((s) => s.trim()) })} placeholder="19:00, 21:00" /></div>
              <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={cfg.auto_shorts} onChange={(e) => setCfg({ ...cfg, auto_shorts: e.target.checked })} /> Gerar Shorts automaticamente</label>
              <button className="btn-primary w-full" onClick={saveCfg}>Salvar</button>

              <div className="pt-2 border-t border-border">
                <p className="text-xs text-text-muted mb-1">Próximos horários sugeridos:</p>
                <ul className="text-sm space-y-1">{slots.map((s, i) => <li key={i} className="font-mono text-[12px]">{fmtDate(s)}</li>)}</ul>
              </div>
            </>
          )}
        </div>
      </div>

      {/* ---- Automação por temas ---- */}
      <div className="card p-5 space-y-4">
        <div>
          <h3 className="heading font-semibold">Automação por temas</h3>
          <p className="text-xs text-text-muted mt-1">
            O sistema gera 1 vídeo por tema nos horários da cadência acima (ex: 2/dia), e cada vídeo para em Aprovações antes de publicar.
          </p>
        </div>

        {!sel ? (
          <p className="text-sm text-text-muted">Selecione uma conta acima para configurar a fila de temas.</p>
        ) : (
          <div className="grid lg:grid-cols-2 gap-6">
            {/* Coluna: adicionar temas */}
            <div className="space-y-3">
              <div>
                <label className="text-xs text-text-muted">Temas</label>
                <textarea
                  className="input mt-1 min-h-[120px] resize-y"
                  rows={5}
                  value={themesText}
                  onChange={(e) => setThemesText(e.target.value)}
                  placeholder="Um tema por linha (ou separados por vírgula). Ex: Copa 1958, Copa 1970, ..."
                />
                <p className="text-[11px] text-text-muted mt-1">{parsedThemes.length} {parsedThemes.length === 1 ? 'tema detectado' : 'temas detectados'}</p>
              </div>
              <div>
                <label className="text-xs text-text-muted">Tipo de conteúdo</label>
                <select className="input mt-1" value={themeType} onChange={(e) => setThemeType(e.target.value)}>
                  {contentTypes.map((c) => <option key={c.value} value={c.value}>{c.label}</option>)}
                </select>
              </div>
              <button className="btn-primary w-full" disabled={busy || parsedThemes.length === 0} onClick={addThemes}>
                {busy ? 'Adicionando...' : 'Adicionar à fila de temas'}
              </button>
            </div>

            {/* Coluna: fila de temas pendentes */}
            <div className="space-y-2">
              <div className="flex items-center justify-between">
                <p className="text-xs text-text-muted">Fila de temas (pendentes): {queue.length}</p>
                {queue.length > 0 && <button className="btn-ghost text-xs" onClick={clearThemes}>🧹 Limpar todos</button>}
              </div>
              {queue.length === 0 ? (
                <div className="card p-6 text-center text-text-muted text-sm">Nenhum tema na fila.</div>
              ) : (
                <ul className="space-y-2">
                  {queue.map((t) => (
                    <li key={t.id} className="card p-2 flex items-center gap-2">
                      <div className="flex-1 min-w-0">
                        <p className="text-sm truncate" title={t.theme}>{t.theme}</p>
                        <span className="text-[11px] text-text-muted">{t.content_type}</span>
                      </div>
                      <span className="badge shrink-0 bg-elevated text-text-muted">{t.status}</span>
                      <button className="btn-ghost text-xs" onClick={() => deleteTheme(t.id)} title="Excluir tema">🗑</button>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
