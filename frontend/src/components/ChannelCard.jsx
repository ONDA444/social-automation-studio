import { useEffect, useState } from 'react'
import { api, mediaUrl } from '../api'
import { PLATFORM_META, INTRO_MODE_OPTIONS, statusMeta, fmtDate } from '../lib'

const todayIso = () => new Date().toISOString().slice(0, 10)

const themeFromChannel = (channel) => ({
  accent_color: channel.visual_theme?.accent_color || '#FFD400',
  stroke_color: channel.visual_theme?.stroke_color || '#000000',
  text_color: channel.visual_theme?.text_color || '#FFFFFF',
  font: channel.visual_theme?.font || 'VeraBd.ttf',
})

export default function ChannelCard({ channel, account, onChange }) {
  const [expanded, setExpanded] = useState(false)
  const [togglingActive, setTogglingActive] = useState(false)

  const [form, setForm] = useState({
    name: channel.name || '',
    intro_mode: channel.intro_mode || 'mixed',
    daily_limit_long: channel.daily_limit_long ?? 1,
    daily_limit_short: channel.daily_limit_short ?? 3,
    posting_window_start: channel.posting_window_start || '08:00',
    posting_window_end: channel.posting_window_end || '23:00',
  })
  const [theme, setTheme] = useState(() => themeFromChannel(channel))
  const [savingSettings, setSavingSettings] = useState(false)
  const [settingsMsg, setSettingsMsg] = useState('')

  // channel is not remounted (keyed by stable channel.id in Channels.jsx), so
  // when the parent refetches and passes a new channel object, resync local
  // fields from it here (same convention as PlatformCard.jsx).
  useEffect(() => {
    setForm({
      name: channel.name || '',
      intro_mode: channel.intro_mode || 'mixed',
      daily_limit_long: channel.daily_limit_long ?? 1,
      daily_limit_short: channel.daily_limit_short ?? 3,
      posting_window_start: channel.posting_window_start || '08:00',
      posting_window_end: channel.posting_window_end || '23:00',
    })
    setTheme(themeFromChannel(channel))
  }, [channel])

  const [agendaDate, setAgendaDate] = useState(todayIso())
  const [agenda, setAgenda] = useState(null)
  const [agendaJobs, setAgendaJobs] = useState([])
  const [loadingAgenda, setLoadingAgenda] = useState(false)
  const [generating, setGenerating] = useState(false)
  const [agendaMsg, setAgendaMsg] = useState('')

  const [previewLine, setPreviewLine] = useState('')
  const [previewFormat, setPreviewFormat] = useState('short')
  const [previewing, setPreviewing] = useState(false)
  const [previewUrl, setPreviewUrl] = useState(null)
  const [previewMsg, setPreviewMsg] = useState('')

  const [refreshing, setRefreshing] = useState(false)
  const [refreshMsg, setRefreshMsg] = useState('')

  const m = account ? (PLATFORM_META[account.platform] || { label: account.platform, color: 'var(--accent)', icon: '?' }) : null

  const toggleActive = async () => {
    setTogglingActive(true)
    try {
      await api.patch(`/channels/${channel.id}`, { active: !channel.active })
      onChange?.()
    } catch (e) {
      alert('Falha ao atualizar canal: ' + e.message)
    } finally {
      setTogglingActive(false)
    }
  }

  const saveSettings = async () => {
    if (!form.name.trim()) return setSettingsMsg('Informe o nome do canal.')
    setSavingSettings(true)
    setSettingsMsg('')
    try {
      await api.patch(`/channels/${channel.id}`, {
        name: form.name.trim(),
        intro_mode: form.intro_mode,
        daily_limit_long: Number(form.daily_limit_long),
        daily_limit_short: Number(form.daily_limit_short),
        posting_window_start: form.posting_window_start,
        posting_window_end: form.posting_window_end,
        visual_theme: theme,
      })
      setSettingsMsg('Salvo.')
      onChange?.()
    } catch (e) {
      setSettingsMsg(e.message || 'Falha ao salvar.')
    } finally {
      setSavingSettings(false)
    }
  }

  const loadAgenda = async (date = agendaDate) => {
    setLoadingAgenda(true)
    setAgendaMsg('')
    try {
      const d = await api.get(`/channels/${channel.id}/agenda?date=${date}`)
      setAgenda(d.session)
      setAgendaJobs(d.jobs || [])
    } catch (e) {
      setAgendaMsg(e.message || 'Falha ao carregar agenda.')
    } finally {
      setLoadingAgenda(false)
    }
  }

  const generateAgenda = async () => {
    setGenerating(true)
    setAgendaMsg('Gerando agenda em segundo plano...')
    try {
      await api.post(`/channels/${channel.id}/agenda/generate?date=${agendaDate}`)
      setTimeout(() => { loadAgenda(agendaDate); setGenerating(false) }, 4000)
    } catch (e) {
      setAgendaMsg(e.message || 'Falha ao iniciar geracao.')
      setGenerating(false)
    }
  }

  const runPreview = async () => {
    if (!previewLine.trim()) return setPreviewMsg('Escreva uma linha de exemplo.')
    setPreviewing(true)
    setPreviewMsg('')
    setPreviewUrl(null)
    try {
      const r = await api.post(`/channels/${channel.id}/preview`, {
        video_format: previewFormat, line: previewLine.trim(),
      })
      setPreviewUrl(mediaUrl(r.preview_path))
    } catch (e) {
      setPreviewMsg(e.message || 'Falha ao gerar preview.')
    } finally {
      setPreviewing(false)
    }
  }

  const runRefresh = async () => {
    setRefreshing(true)
    setRefreshMsg('')
    try {
      const r = await api.post(`/channels/${channel.id}/refresh`)
      setRefreshMsg(r.status === 'started' ? 'Recuradoria iniciada em segundo plano.' : 'Recuradoria concluida.')
    } catch (e) {
      setRefreshMsg(e.message || 'Falha ao iniciar recuradoria.')
    } finally {
      setRefreshing(false)
    }
  }

  return (
    <div className="card card-hover p-3 sm:p-3.5" style={{ borderColor: channel.active ? 'var(--border-glass)' : 'rgba(217,154,61,0.46)' }}>
      <div className="flex items-start gap-3">
        <div
          className="w-10 h-10 rounded-btn flex items-center justify-center font-black data shrink-0"
          style={{ color: theme.accent_color, background: 'var(--accent-dim)', border: '1px solid var(--border-glass)' }}
        >
          {m ? m.icon : 'CH'}
        </div>

        <div className="min-w-0 flex-1">
          <div className="flex items-start justify-between gap-2">
            <div className="min-w-0">
              <h3 className="heading text-base leading-tight truncate" title={channel.name}>{channel.name}</h3>
              <p className="text-xs mt-0.5 truncate" style={{ color: 'var(--text-muted)' }}>
                {account ? `${m.label} - ${account.display_name}` : 'Conta vinculada nao encontrada'}
              </p>
            </div>
            <span className="badge shrink-0" style={{ color: channel.active ? 'var(--success)' : 'var(--text-muted)', background: 'var(--bg-elevated)' }}>
              <span className="w-1.5 h-1.5 rounded-full" style={{ background: channel.active ? 'var(--success)' : 'var(--text-muted)' }} />
              {channel.active ? 'Ativo' : 'Pausado'}
            </span>
          </div>

          <div className="mt-3 rounded-btn px-3 py-2" style={{ background: 'var(--bg-elevated)', border: '1px solid var(--border-glass)' }}>
            <div className="grid grid-cols-3 items-center gap-3">
              <div>
                <p className="text-[10px] font-semibold uppercase" style={{ color: 'var(--text-dim)', letterSpacing: '.06em' }}>Longos/dia</p>
                <p className="data text-sm font-bold">{channel.daily_limit_long}</p>
              </div>
              <div>
                <p className="text-[10px] font-semibold uppercase" style={{ color: 'var(--text-dim)', letterSpacing: '.06em' }}>Shorts/dia</p>
                <p className="data text-sm font-bold">{channel.daily_limit_short}</p>
              </div>
              <div className="min-w-0">
                <p className="text-[10px] font-semibold uppercase" style={{ color: 'var(--text-dim)', letterSpacing: '.06em' }}>Janela</p>
                <p className="data text-sm font-bold truncate">{channel.posting_window_start}-{channel.posting_window_end}</p>
              </div>
            </div>
          </div>

          <div className="mt-3 flex flex-wrap gap-2">
            <button className="btn-ghost btn-sm" disabled={togglingActive} onClick={toggleActive}>
              {togglingActive ? 'Aguarde...' : (channel.active ? 'Pausar' : 'Ativar')}
            </button>
            <button className="btn-ghost btn-sm" onClick={() => setExpanded((v) => !v)}>
              {expanded ? 'Fechar detalhes' : 'Detalhes'}
            </button>
          </div>
        </div>
      </div>

      {expanded && (
        <div className="slide-down mt-4 pt-4 space-y-5" style={{ borderTop: '1px solid var(--border-glass)' }}>
          <div className="space-y-3">
            <p className="text-xs font-semibold uppercase" style={{ color: 'var(--text-muted)', letterSpacing: '.06em' }}>Ajustes</p>
            <div className="grid sm:grid-cols-2 gap-3">
              <label className="space-y-1">
                <span className="text-xs font-semibold" style={{ color: 'var(--text-muted)' }}>Nome</span>
                <input className="input" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
              </label>
              <label className="space-y-1">
                <span className="text-xs font-semibold" style={{ color: 'var(--text-muted)' }}>Nicho (da conta)</span>
                <input className="input" disabled value={account?.niche || 'Nao definido'}
                  title="O nicho pertence a conta -- edite em Plataformas." />
              </label>
              <label className="space-y-1 sm:col-span-2">
                <span className="text-xs font-semibold" style={{ color: 'var(--text-muted)' }}>Modo de intro</span>
                <select className="input" value={form.intro_mode} onChange={(e) => setForm({ ...form, intro_mode: e.target.value })}>
                  {INTRO_MODE_OPTIONS.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
                </select>
              </label>
              <label className="space-y-1">
                <span className="text-xs font-semibold" style={{ color: 'var(--text-muted)' }}>Longos por dia</span>
                <input type="number" min="0" max="20" className="input" value={form.daily_limit_long}
                  onChange={(e) => setForm({ ...form, daily_limit_long: e.target.value })} />
              </label>
              <label className="space-y-1">
                <span className="text-xs font-semibold" style={{ color: 'var(--text-muted)' }}>Shorts por dia</span>
                <input type="number" min="0" max="50" className="input" value={form.daily_limit_short}
                  onChange={(e) => setForm({ ...form, daily_limit_short: e.target.value })} />
              </label>
              <label className="space-y-1">
                <span className="text-xs font-semibold" style={{ color: 'var(--text-muted)' }}>Janela - inicio</span>
                <input type="time" className="input" value={form.posting_window_start}
                  onChange={(e) => setForm({ ...form, posting_window_start: e.target.value })} />
              </label>
              <label className="space-y-1">
                <span className="text-xs font-semibold" style={{ color: 'var(--text-muted)' }}>Janela - fim</span>
                <input type="time" className="input" value={form.posting_window_end}
                  onChange={(e) => setForm({ ...form, posting_window_end: e.target.value })} />
              </label>
            </div>

            <p className="text-xs font-semibold uppercase pt-1" style={{ color: 'var(--text-muted)', letterSpacing: '.06em' }}>Tema visual</p>
            <div className="grid grid-cols-3 sm:grid-cols-4 gap-3">
              <label className="space-y-1">
                <span className="text-xs font-semibold" style={{ color: 'var(--text-muted)' }}>Destaque</span>
                <input type="color" className="input p-1 h-9" value={theme.accent_color}
                  onChange={(e) => setTheme({ ...theme, accent_color: e.target.value })} />
              </label>
              <label className="space-y-1">
                <span className="text-xs font-semibold" style={{ color: 'var(--text-muted)' }}>Contorno</span>
                <input type="color" className="input p-1 h-9" value={theme.stroke_color}
                  onChange={(e) => setTheme({ ...theme, stroke_color: e.target.value })} />
              </label>
              <label className="space-y-1">
                <span className="text-xs font-semibold" style={{ color: 'var(--text-muted)' }}>Texto</span>
                <input type="color" className="input p-1 h-9" value={theme.text_color}
                  onChange={(e) => setTheme({ ...theme, text_color: e.target.value })} />
              </label>
              <label className="space-y-1 col-span-3 sm:col-span-1">
                <span className="text-xs font-semibold" style={{ color: 'var(--text-muted)' }}>Fonte</span>
                <input className="input" value={theme.font} onChange={(e) => setTheme({ ...theme, font: e.target.value })} />
              </label>
            </div>

            <div className="flex items-center gap-3">
              <button className="btn-primary btn-sm" disabled={savingSettings} onClick={saveSettings}>
                {savingSettings ? 'Salvando...' : 'Salvar ajustes'}
              </button>
              {settingsMsg && <span className="text-[11px]" style={{ color: settingsMsg === 'Salvo.' ? 'var(--success)' : 'var(--warning)' }}>{settingsMsg}</span>}
            </div>
          </div>

          <div className="space-y-2 pt-4" style={{ borderTop: '1px solid var(--border-glass)' }}>
            <p className="text-xs font-semibold uppercase" style={{ color: 'var(--text-muted)', letterSpacing: '.06em' }}>Preview visual</p>
            <div className="flex flex-col sm:flex-row gap-2">
              <input className="input flex-1" placeholder="Linha de exemplo do overlay/intro"
                value={previewLine} onChange={(e) => setPreviewLine(e.target.value)} />
              <select className="input sm:w-32" value={previewFormat} onChange={(e) => setPreviewFormat(e.target.value)}>
                <option value="short">Short</option>
                <option value="long">Longo</option>
              </select>
              <button className="btn-ghost btn-sm shrink-0" disabled={previewing} onClick={runPreview}>
                {previewing ? 'Gerando...' : 'Gerar preview'}
              </button>
            </div>
            {previewMsg && <p className="text-[11px]" style={{ color: 'var(--warning)' }}>{previewMsg}</p>}
            {previewUrl && (
              <img src={previewUrl} alt="Preview do tema visual" className="mt-1 rounded-btn max-w-full sm:max-w-xs border" style={{ borderColor: 'var(--border-glass)' }} />
            )}
          </div>

          <div className="space-y-2 pt-4" style={{ borderTop: '1px solid var(--border-glass)' }}>
            <div className="flex items-center justify-between gap-2">
              <p className="text-xs font-semibold uppercase" style={{ color: 'var(--text-muted)', letterSpacing: '.06em' }}>Agenda do dia</p>
              <input type="date" className="input w-auto text-xs py-1" value={agendaDate}
                onChange={(e) => setAgendaDate(e.target.value)} />
            </div>
            <div className="flex flex-wrap gap-2">
              <button className="btn-ghost btn-sm" disabled={loadingAgenda} onClick={() => loadAgenda(agendaDate)}>
                {loadingAgenda ? 'Carregando...' : 'Ver agenda'}
              </button>
              <button className="btn-ghost btn-sm" disabled={generating} onClick={generateAgenda}>
                {generating ? 'Gerando...' : 'Gerar agenda'}
              </button>
            </div>
            {agendaMsg && <p className="text-[11px]" style={{ color: 'var(--warning)' }}>{agendaMsg}</p>}
            {agenda && (
              <div className="rounded-btn p-3" style={{ background: 'var(--bg-elevated)', border: '1px solid var(--border-glass)' }}>
                <div className="flex items-center justify-between gap-2 mb-2">
                  <span className="text-xs font-semibold">Sessao {agenda.date}</span>
                  <span className="badge" style={{ background: 'var(--accent-dim)', color: 'var(--accent)' }}>{agenda.status}</span>
                </div>
                {agendaJobs.length === 0 ? (
                  <p className="text-[11px]" style={{ color: 'var(--text-muted)' }}>Nenhum item planejado para essa data.</p>
                ) : (
                  <ul className="space-y-1.5">
                    {agendaJobs.map((j) => {
                      const s = statusMeta(j.status)
                      return (
                        <li key={j.job_id} className="flex items-center gap-2 text-[11px]">
                          <span className="badge shrink-0" style={{ background: s.color + '22', color: s.color }}>{s.label}</span>
                          <span className="flex-1 min-w-0 truncate" title={j.title || `job ${j.job_id}`}>{j.title || `job ${j.job_id}`}</span>
                          <span className="shrink-0" style={{ color: 'var(--text-muted)' }}>{j.video_format === 'short' ? 'Short' : 'Longo'}</span>
                          {j.scheduled_at && <span className="shrink-0" style={{ color: 'var(--text-muted)' }}>{fmtDate(j.scheduled_at)}</span>}
                        </li>
                      )
                    })}
                  </ul>
                )}
              </div>
            )}
          </div>

          <div className="space-y-2 pt-4" style={{ borderTop: '1px solid var(--border-glass)' }}>
            <p className="text-xs font-semibold uppercase" style={{ color: 'var(--text-muted)', letterSpacing: '.06em' }}>Recuradoria</p>
            <p className="text-[11px]" style={{ color: 'var(--text-muted)' }}>
              Reaplica o tema visual atual aos videos ja curados deste canal (sem regravar do zero).
            </p>
            <button className="btn-ghost btn-sm" disabled={refreshing} onClick={runRefresh}>
              {refreshing ? 'Iniciando...' : 'Recuradoria de novo tema'}
            </button>
            {refreshMsg && <p className="text-[11px]" style={{ color: 'var(--success)' }}>{refreshMsg}</p>}
          </div>
        </div>
      )}
    </div>
  )
}
