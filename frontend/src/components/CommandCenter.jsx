import { useNavigate } from 'react-router-dom'
import { useDashboardStatus } from '../App.jsx'
import { PLATFORM_META } from '../lib'

// Command Center — LIVE SYSTEM STATUS do Dashboard. Consome o poll compartilhado
// (useDashboardStatus): nenhuma request extra, atualiza a cada ~8s e a cada
// evento do pipeline. Tudo aqui vem de /dashboard + /dashboard/health reais.

const CHECK_LABELS = {
  database: 'Banco de dados',
  redis: 'Redis',
  ffmpeg: 'FFmpeg',
  disk: 'Disco',
  llm: 'IA (LLM)',
  scheduler: 'Agendador',
  pipeline_worker: 'Worker de pipeline',
  edge_tts: 'Voz (edge-tts)',
}

const TONE = {
  green: { color: 'var(--success)', bg: 'color-mix(in srgb, var(--success) 12%, transparent)' },
  yellow: { color: 'var(--warning)', bg: 'color-mix(in srgb, var(--warning) 12%, transparent)' },
  red: { color: 'var(--error)', bg: 'color-mix(in srgb, var(--error) 12%, transparent)' },
}

function CheckChip({ name, check }) {
  const tone = TONE[check.status] || TONE.yellow
  return (
    <div className="flex items-center gap-2 rounded-btn px-3 py-2 min-w-0"
      style={{ background: tone.bg, border: `1px solid color-mix(in srgb, ${tone.color} 25%, transparent)` }}
      title={check.detail}>
      <span className="relative flex h-2 w-2 shrink-0">
        {check.status === 'green' && (
          <span className="absolute inline-flex h-full w-full rounded-full opacity-60 animate-ping" style={{ background: tone.color }} />
        )}
        <span className="relative inline-flex rounded-full h-2 w-2" style={{ background: tone.color }} />
      </span>
      <span className="text-xs font-semibold truncate" style={{ color: 'var(--text-primary)' }}>
        {CHECK_LABELS[name] || name}
      </span>
      <span className="text-[10px] ml-auto shrink-0 hidden sm:inline" style={{ color: tone.color }}>
        {check.status === 'green' ? 'ok' : check.status === 'yellow' ? 'atenção' : 'falha'}
      </span>
    </div>
  )
}

export default function CommandCenter() {
  const { data, healthDetail, stale } = useDashboardStatus()
  const nav = useNavigate()

  const checks = healthDetail?.checks || {}
  const sc = data?.status_counts || {}
  const connected = data?.accounts_connected || {}
  const recentErrors = data?.recent_errors || []
  const queueDepth = (sc.queued || 0)
  const processing = (sc.processing || 0)
  const publishing = (sc.publishing || 0)

  return (
    <div className="rounded-card p-5 space-y-4 fade-in"
      style={{ background: 'var(--bg-surface)', border: '1px solid var(--border)', boxShadow: 'var(--shadow-card)' }}>
      <div className="flex items-center gap-3">
        <h3 className="heading font-extrabold">Command Center</h3>
        <span className="text-xs" style={{ color: 'var(--text-muted)' }}>status ao vivo do sistema</span>
        <div className="flex-1 h-px" style={{ background: 'var(--border)' }} />
        {stale && (
          <span className="badge text-[10px]" style={{ background: 'var(--accent-dim)', color: 'var(--warning)', border: '1px solid var(--border-glow)' }}>
            reconectando…
          </span>
        )}
        <button className="btn-ghost btn-sm text-xs" onClick={() => nav('/system')}>Detalhes →</button>
      </div>

      {/* Capacidade: o que a fila está fazendo agora */}
      <div className="grid grid-cols-3 gap-3">
        {[
          { label: 'Na fila', value: queueDepth, to: '/queue' },
          { label: 'Produzindo', value: processing, to: '/queue' },
          { label: 'Publicando', value: publishing, to: '/queue' },
        ].map((s) => (
          <button key={s.label} onClick={() => nav(s.to)} className="rounded-btn p-3 text-left transition-colors hover:brightness-110"
            style={{ background: 'var(--bg-elevated)', border: '1px solid var(--border)' }}>
            <p className="text-[10px] uppercase font-semibold" style={{ color: 'var(--text-dim)', letterSpacing: '.06em' }}>{s.label}</p>
            <p className="heading text-2xl font-black tabular-nums" style={{ color: s.value > 0 ? 'var(--accent)' : 'var(--text-primary)' }}>
              {s.value}
            </p>
          </button>
        ))}
      </div>

      {/* Componentes */}
      <div>
        <p className="text-[10px] uppercase font-bold mb-2" style={{ color: 'var(--text-dim)', letterSpacing: '.07em' }}>Componentes</p>
        {Object.keys(checks).length === 0
          ? <div className="grid grid-cols-2 lg:grid-cols-4 gap-2">{Array.from({ length: 4 }).map((_, i) => <div key={i} className="h-9 skeleton rounded-btn" />)}</div>
          : (
            <div className="grid grid-cols-2 lg:grid-cols-4 gap-2">
              {Object.entries(checks).map(([name, check]) => <CheckChip key={name} name={name} check={check} />)}
            </div>
          )}
      </div>

      {/* Contas conectadas por plataforma */}
      <div>
        <p className="text-[10px] uppercase font-bold mb-2" style={{ color: 'var(--text-dim)', letterSpacing: '.07em' }}>Contas conectadas</p>
        <div className="flex flex-wrap gap-2">
          {['youtube', 'tiktok', 'instagram'].map((p) => {
            const m = PLATFORM_META[p] || { label: p, color: 'var(--accent)', icon: p }
            const n = connected[p] || 0
            return (
              <button key={p} onClick={() => nav('/platforms')}
                className="flex items-center gap-2 rounded-pill px-3 py-1.5 text-xs font-semibold transition-colors hover:brightness-110"
                style={{
                  background: n > 0 ? `${m.color}18` : 'var(--bg-elevated)',
                  border: `1px solid ${n > 0 ? m.color + '44' : 'var(--border)'}`,
                  color: n > 0 ? m.color : 'var(--text-dim)',
                }}
                title={n > 0 ? `${n} conta(s) ativa(s) no ${m.label}` : `Nenhuma conta ${m.label} conectada`}>
                {m.icon} {m.label}
                <span className="font-mono font-black">{n}</span>
              </button>
            )
          })}
        </div>
      </div>

      {/* Falhas recentes */}
      {recentErrors.length > 0 && (
        <div>
          <p className="text-[10px] uppercase font-bold mb-2" style={{ color: 'var(--error)', letterSpacing: '.07em' }}>Falhas recentes</p>
          <div className="space-y-1.5">
            {recentErrors.map((e) => (
              <button key={e.id} onClick={() => nav('/queue')}
                className="w-full text-left flex items-center gap-2.5 rounded-btn px-3 py-2 transition-colors hover:brightness-110"
                style={{ background: 'color-mix(in srgb, var(--error) 8%, transparent)', border: '1px solid color-mix(in srgb, var(--error) 20%, transparent)' }}>
                <span className="text-[10px] font-mono shrink-0" style={{ color: 'var(--error)' }}>#{e.id}</span>
                <span className="text-xs truncate font-medium" style={{ color: 'var(--text-primary)' }}>{e.title}</span>
                {e.current_agent && (
                  <span className="text-[10px] shrink-0 hidden sm:inline" style={{ color: 'var(--text-muted)' }}>etapa: {e.current_agent}</span>
                )}
              </button>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}
