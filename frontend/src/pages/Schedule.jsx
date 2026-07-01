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
  { v: 'fixed',          label: 'Fixo',         desc: 'Voce define os horarios exatos.' },
  { v: 'smart',          label: 'Inteligente',  desc: 'O sistema escolhe os melhores horarios automaticamente.' },
  { v: 'trending_aware', label: 'Trending',     desc: 'Publica logo que um trending e detectado.' },
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
  const [saveState, setSaveState] = useState({ status: 'idle', message: '' })

  const [themesText, setThemesText]   = useState('')
  const [themeType, setThemeType]     = useState('film_recap_ai_images')
  const [themeFormat, setThemeFormat] = useState('long')
  const [contentTypes, setContentTypes] = useState(FALLBACK_CONTENT_TYPES)
  const [queue, setQueue]   = useState([])
  const [history, setHistory] = useState([])   // consumed themes (already turned into videos)
  const [showHistory, setShowHistory] = useState(false)
  const [busy, setBusy]     = useState(false)
  const [driveStatus, setDriveStatus] = useState(null)
  const [driveVideos, setDriveVideos] = useState([])
  const [driveVideoStats, setDriveVideoStats] = useState({})
  const [driveSyncing, setDriveSyncing] = useState(false)
  const [driveCfg, setDriveCfg] = useState({
    video_source_mode: 'ai',
    drive_folder_url: '',
    drive_niche: '',
    drive_recursive: true,
  })

  const selAccount = accounts.find((a) => String(a.id) === sel)
  const platMeta   = selAccount ? (PLATFORM_META[selAccount.platform] || {}) : {}
  const saveStateClass = saveState.status === 'saved'
    ? 'border-success/25 bg-success/10 text-success'
    : saveState.status === 'error'
      ? 'border-danger/25 bg-danger/10 text-danger'
      : 'border-warning/25 bg-warning/10 text-text-muted'

  const markDirty = () => {
    setSaveState((s) => s.status === 'saving' ? s : { status: 'dirty', message: 'Alteracoes ainda nao salvas.' })
  }

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
    const acct = accounts.find((a) => String(a.id) === sel)
    if (acct) {
      setDriveCfg({
        video_source_mode: acct.video_source_mode || 'ai',
        drive_folder_url: acct.drive_folder_url || acct.drive_folder_id || '',
        drive_niche: acct.drive_niche || acct.niche || '',
        drive_recursive: acct.drive_recursive !== false,
      })
    }
    loadDrive()
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
    if (!sel) return alert('Selecione uma conta primeiro')
    setSaving(true)
    setSaveState({ status: 'saving', message: 'Salvando configuracao...' })
    try {
      const savedCfg = await api.put(`/schedule/config/${sel}`, cfg, { timeoutMs: 20000 })
      const savedAccount = await api.patch(`/accounts/${sel}`, driveCfg, { timeoutMs: 20000 })
      setCfg((p) => ({ ...p, ...savedCfg, post_times: savedCfg.post_times?.length ? savedCfg.post_times : p.post_times }))
      setDriveCfg({
        video_source_mode: savedAccount.video_source_mode || 'ai',
        drive_folder_url: savedAccount.drive_folder_url || savedAccount.drive_folder_id || '',
        drive_niche: savedAccount.drive_niche || savedAccount.niche || '',
        drive_recursive: savedAccount.drive_recursive !== false,
      })
      const d = await api.get(`/schedule/${sel}/slots?count=6&mode=${savedCfg.mode || cfg.mode}`, { timeoutMs: 20000 })
      setSlots(d.slots || [])
      setSlotsMeta(d)
      const fresh = await api.get('/accounts', { timeoutMs: 20000 })
      setAccounts(fresh.accounts || [])
      await loadDrive()
      setSaveState({ status: 'saved', message: `Salvo com sucesso as ${new Date().toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit' })}.` })
    } catch (e) {
      setSaveState({ status: 'error', message: e.message || 'Falha ao salvar configuracao.' })
      alert(e.message)
    } finally { setSaving(false) }
  }

  const openOAuth = (acct) => {
    window.open(`${BASE_API}/auth/${acct.platform}/start?account_id=${acct.id}`, 'oauth', 'width=500,height=640')
  }

  const openDriveOAuth = async () => {
    const popup = window.open('', 'drive-oauth', 'width=520,height=680')
    try {
      const r = await api.get('/drive-library/auth/start')
      if (popup) popup.location = r.auth_url
      else window.location.href = r.auth_url
    } catch (e) {
      if (popup) popup.close()
      alert(e.message)
    }
  }

  const copyDriveRedirect = async () => {
    if (!driveStatus?.redirect_uri) return
    try {
      await navigator.clipboard.writeText(driveStatus.redirect_uri)
      alert('Callback do Drive copiado.')
    } catch {
      alert(driveStatus.redirect_uri)
    }
  }

  const disconnectDrive = async () => {
    if (!confirm('Desconectar o Google Drive deste sistema? Os canais em modo Drive/Misto vao parar de sincronizar ate conectar novamente.')) return
    try {
      await api.post('/drive-library/disconnect')
      setDriveVideos([])
      await loadDrive()
    } catch (e) {
      alert(e.message)
    }
  }

  const loadDrive = async () => {
    if (!sel) return
    try {
      const [status, videos] = await Promise.all([
        api.get('/drive-library/status').catch(() => null),
        api.get(`/drive-library/videos?account_id=${sel}&limit=500`).catch(() => ({ videos: [] })),
      ])
      setDriveStatus(status)
      setDriveVideos(videos.videos || [])
      setDriveVideoStats(videos.stats || {})
    } catch {
      setDriveVideos([])
      setDriveVideoStats({})
    }
  }

  const syncDrive = async () => {
    if (!sel) return alert('Selecione uma conta primeiro')
    if (!driveStatus?.has_credentials && !driveStatus?.api_key_configured) {
      await openDriveOAuth()
      return
    }
    setDriveSyncing(true)
    try {
      await api.patch(`/accounts/${sel}`, driveCfg)
      const r = await api.post(`/drive-library/accounts/${sel}/sync`)
      alert(`Drive sincronizado: ${r.imported || 0} novo(s), ${r.updated || 0} atualizado(s).`)
      const fresh = await api.get('/accounts')
      setAccounts(fresh.accounts || [])
      await loadDrive()
    } catch (e) { alert(e.message) } finally { setDriveSyncing(false) }
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
    try { await api.post('/themes/bulk-delete', { status: 'pending', account_id: Number(sel) }); loadQueue() } catch (e) { alert(e.message) }
  }

  return (
    <div className="space-y-6 fade-in">
      <PageHeader title="Agenda" sub="Organize horarios, escolha entre IA ou Drive e deixe a fila pronta para publicar." />

      <div className="grid lg:grid-cols-3 gap-6">
        {/* Calendário */}
        <div className="lg:col-span-2">
          <CalendarView events={events} />
        </div>

        {/* Painel de configuração */}
        <div className="card p-5 space-y-4 h-fit">
          <div>
            <h3 className="heading font-extrabold text-base">Cadencia do canal</h3>
            <p className="text-xs text-text-muted mt-1">Conta, horarios, fonte dos videos e proximas publicacoes.</p>
          </div>

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
                      onClick={() => { setCfg({ ...cfg, mode: m.v }); markDirty() }}
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
                  onChange={(e) => { setCfg({ ...cfg, videos_per_day: Number(e.target.value) }); markDirty() }} />
              </div>

              {/* Horários — só visível no modo fixo */}
              {cfg.mode === 'fixed' && (
                <div className="slide-down">
                  <label className="text-xs text-text-muted mb-1 block">Horários (vírgula)</label>
                  <input className="input font-mono"
                    value={(cfg.post_times || []).join(', ')}
                    onChange={(e) => { setCfg({ ...cfg, post_times: e.target.value.split(',').map((s) => s.trim()) }); markDirty() }}
                    placeholder="19:00, 21:00" />
                  <p className="text-[11px] text-text-muted mt-1">Ex: 10:00, 14:30, 19:00</p>
                </div>
              )}

              {/* Gerar Shorts automático */}
              <div className="p-3 rounded-xl border border-border bg-elevated/40">
                <label className="flex items-center gap-2.5 text-sm cursor-pointer select-none">
                  <input type="checkbox" className="accent-accent w-4 h-4 shrink-0"
                    checked={cfg.auto_shorts}
                    onChange={(e) => { setCfg({ ...cfg, auto_shorts: e.target.checked }); markDirty() }} />
                  <span className="font-medium">Gerar Shorts automaticamente</span>
                </label>
                <p className="text-[11px] text-text-muted mt-1.5 ml-[26px]">
                  Cada vídeo longo também gera uma versão vertical 9:16 (Shorts/Reels) no mesmo slot — sem precisar criar um tema separado.
                </p>
              </div>

              <div className="p-4 rounded-card border border-border space-y-4" style={{ background: 'var(--bg-surface)' }}>
                <div className="flex items-start justify-between gap-3">
                  <div>
                    <p className="text-sm font-bold">Fonte dos videos</p>
                    <p className="text-xs text-text-muted mt-0.5">
                      Escolha como esse canal vai produzir: temas com IA, biblioteca do Drive ou os dois.
                    </p>
                  </div>
                  <span className="badge shrink-0"
                    style={{ background: 'var(--accent-dim)', color: 'var(--accent)', border: '1px solid rgba(31,138,91,0.22)' }}>
                    {driveCfg.video_source_mode === 'ai' ? 'IA' : driveCfg.video_source_mode === 'drive' ? 'Drive' : 'Misto'}
                  </span>
                </div>

                <div className="grid grid-cols-3 gap-1 p-1 rounded-btn bg-elevated border border-border">
                  {[
                    { v: 'ai', label: 'IA', hint: 'gera' },
                    { v: 'drive', label: 'Drive', hint: 'prontos' },
                    { v: 'mixed', label: 'Misto', hint: 'fallback' },
                  ].map((m) => (
                    <button key={m.v} type="button"
                      onClick={() => { setDriveCfg({ ...driveCfg, video_source_mode: m.v }); markDirty() }}
                      className={`py-2 px-1 rounded-btn font-semibold transition-all duration-150 leading-tight ${
                        driveCfg.video_source_mode === m.v
                          ? 'bg-accent text-white shadow-sm'
                          : 'text-text-muted hover:text-text-primary'
                      }`}>
                      <span className="block text-xs">{m.label}</span>
                      <span className="block text-[10px] opacity-70">{m.hint}</span>
                    </button>
                  ))}
                </div>

                {driveCfg.video_source_mode !== 'ai' && (
                  <div className="space-y-2 slide-down">
                    <div className="flex items-center justify-between gap-2 rounded-btn border border-border px-3 py-2" style={{ background: 'var(--bg-elevated)' }}>
                      <div>
                        <p className="text-xs font-semibold">Google Drive</p>
                        <p className="text-[11px] text-text-muted">
                          {driveStatus?.has_credentials || driveStatus?.api_key_configured ? 'Conexao unica da biblioteca do Drive.' : 'Conecte para ler pastas privadas.'}
                        </p>
                      </div>
                      {driveStatus?.has_credentials ? (
                        <button type="button" className="btn btn-ghost btn-sm" onClick={disconnectDrive}>
                          Desconectar
                        </button>
                      ) : (
                        <button type="button" className="btn btn-ghost btn-sm" onClick={openDriveOAuth}>
                          Conectar Drive
                        </button>
                      )}
                    </div>

                    {driveStatus?.has_credentials && (
                      <div className="rounded-btn border border-border p-3" style={{ background: 'var(--bg-elevated)' }}>
                        <p className="text-[11px] text-text-muted">
                          Esta conexao vale para todos os canais. Cada canal decide se usa IA, Drive ou Misto, mas a conta Google Drive conectada e uma so.
                        </p>
                      </div>
                    )}

                    {!driveStatus?.has_credentials && driveStatus?.redirect_uri && (
                      <div className="rounded-btn border border-warning/25 bg-warning/10 p-3">
                        <p className="text-[11px] font-semibold text-text-primary">
                          Callback para autorizar no Google Cloud
                        </p>
                        <div className="mt-1 flex items-center gap-2">
                          <code className="min-w-0 flex-1 truncate rounded-md border border-border px-2 py-1 text-[10px] text-text-muted" style={{ background: 'var(--bg-elevated)' }}>
                            {driveStatus.redirect_uri}
                          </code>
                          <button type="button" className="btn btn-ghost btn-sm shrink-0" onClick={copyDriveRedirect}>
                            Copiar
                          </button>
                        </div>
                        <p className="mt-1 text-[10px] text-text-muted">
                          Se o Google mostrar redirect_uri_mismatch, adicione esse callback em APIs e servicos &gt; Credenciais &gt; OAuth Client.
                        </p>
                      </div>
                    )}

                    <div>
                      <label className="text-xs text-text-muted mb-1 block">Pasta do Drive</label>
                      <input className="input text-xs" value={driveCfg.drive_folder_url}
                        onChange={(e) => { setDriveCfg({ ...driveCfg, drive_folder_url: e.target.value }); markDirty() }}
                        placeholder="https://drive.google.com/drive/folders/..." />
                    </div>

                    <div className="grid grid-cols-2 gap-2">
                      <div>
                        <label className="text-xs text-text-muted mb-1 block">Nicho</label>
                        <input className="input text-xs" value={driveCfg.drive_niche}
                          onChange={(e) => { setDriveCfg({ ...driveCfg, drive_niche: e.target.value }); markDirty() }}
                          placeholder={selAccount?.niche || 'saude, filmes, memes...'} />
                      </div>
                      <label className="flex items-center gap-2 text-xs text-text-muted mt-6">
                        <input type="checkbox" className="accent-accent"
                          checked={driveCfg.drive_recursive}
                          onChange={(e) => { setDriveCfg({ ...driveCfg, drive_recursive: e.target.checked }); markDirty() }} />
                        Ler subpastas
                      </label>
                    </div>

                    <button type="button" className="btn btn-ghost w-full text-xs" disabled={driveSyncing} onClick={syncDrive}>
                      {driveSyncing ? 'Sincronizando...' : 'Sincronizar pasta agora'}
                    </button>

                    <div className="rounded-card border border-border p-3" style={{ background: 'var(--bg-surface)' }}>
                      <div className="flex items-center justify-between gap-3">
                        <div>
                          <p className="text-xs font-semibold">Estoque indexado</p>
                          <p className="text-[11px] text-text-muted">
                            Videos prontos em ordem de uso. O agendador reserva de cima para baixo e nao repete usados.
                          </p>
                        </div>
                        <span className="heading text-xl font-extrabold" style={{ color: 'var(--accent)' }}>{driveVideoStats.available || 0}</span>
                      </div>
                      {driveVideos.length > 0 && (
                        <ul className="mt-3 space-y-1 max-h-72 overflow-y-auto pr-1">
                          {driveVideos.map((v, idx) => (
                            <li key={v.id} className="grid grid-cols-[34px_minmax(0,1fr)_72px] items-center gap-2 rounded-md px-2 py-1 text-[11px]"
                              style={{ background: v.status === 'available' ? 'var(--bg-elevated)' : 'transparent' }}>
                              <span className="data text-text-muted">#{idx + 1}</span>
                              <span className="truncate" title={`${v.folder_path || ''} / ${v.name}`}>{v.name}</span>
                              <span className="text-text-muted shrink-0 text-right">{v.status}</span>
                            </li>
                          ))}
                        </ul>
                      )}
                      {(driveVideoStats.available || 0) > driveVideos.length && (
                        <p className="mt-2 text-[11px] text-text-muted">
                          Mostrando {driveVideos.length} de {driveVideoStats.available} disponiveis.
                        </p>
                      )}
                    </div>
                  </div>
                )}
              </div>

              <button className="btn-primary w-full" onClick={saveCfg} disabled={saving}>
                {saving ? 'Salvando...' : saveState.status === 'saved' ? 'Salvo' : 'Salvar configuracao'}
              </button>
              {saveState.message && (
                <div className={`rounded-btn border px-3 py-2 text-[11px] ${saveStateClass}`}>
                  {saveState.message}
                </div>
              )}

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
                  <button className="btn-ghost text-xs" onClick={clearThemes}>🧹 Limpar fila</button>
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
