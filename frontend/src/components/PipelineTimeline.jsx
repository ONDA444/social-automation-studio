import { useEffect, useMemo, useState } from 'react'
import { api } from '../api'
import { useWs } from '../App.jsx'

// Linha do tempo do pipeline — mesma ordem do orquestrador
// (backend/agents/orchestrator.py). Cada etapa mostra status, duração real
// (started→completed nos eventos), tentativas e o último erro.
const STAGES = [
  ['research', 'Pesquisa'],
  ['scriptwriter', 'Roteiro'],
  ['hook_optimizer', 'Gancho'],
  ['retention_engineer', 'Retenção'],
  ['packaging_strategist', 'Empacotamento'],
  ['narrator', 'Narração'],
  ['visuals', 'Visuais'],
  ['editing_director', 'Direção de edição'],
  ['music_curator', 'Música'],
  ['caption_agent', 'Legendas'],
  ['video_editor', 'Edição'],
  ['shorts_factory', 'Shorts'],
  ['shorts_hook', 'Gancho Shorts'],
  ['shorts_strategist', 'Estratégia Shorts'],
  ['seo_agent', 'SEO'],
  ['quality_control', 'Qualidade'],
  ['compliance_agent', 'Compliance'],
]

const STATE_STYLE = {
  done:    { color: 'var(--success)', icon: '✓', label: 'concluída' },
  current: { color: 'var(--accent)', icon: '●', label: 'em execução' },
  error:   { color: 'var(--error)', icon: '✕', label: 'falhou' },
  pending: { color: 'var(--text-dim)', icon: '○', label: 'pendente' },
}

function fmtDuration(ms) {
  if (ms == null || ms < 0) return null
  const s = Math.round(ms / 1000)
  if (s < 60) return `${s}s`
  return `${Math.floor(s / 60)}m ${s % 60}s`
}

// Deriva o estado de cada etapa a partir dos eventos do job (histórico do log
// + eventos WS ao vivo). Ordena cronologicamente antes de reduzir.
function buildStages(events, job) {
  const byAgent = new Map()
  const sorted = [...events].sort((a, b) => new Date(a.at || 0) - new Date(b.at || 0))
  for (const ev of sorted) {
    if (ev.type !== 'agent_event' || !ev.agent) continue
    const cur = byAgent.get(ev.agent) || { retries: 0, startedAt: null, doneAt: null, error: null, lastMessage: '' }
    if (ev.status === 'started') { cur.startedAt = ev.at || cur.startedAt; cur.error = null }
    if (ev.status === 'retry') cur.retries += 1
    if (ev.status === 'completed') { cur.doneAt = ev.at || new Date().toISOString() }
    if (ev.status === 'error') cur.error = ev.error || ev.message || 'falhou'
    if (ev.message) cur.lastMessage = ev.message
    byAgent.set(ev.agent, cur)
  }

  const currentAgent = job.status === 'processing' ? job.current_agent : null
  return STAGES.map(([key, label]) => {
    const info = byAgent.get(key)
    let state = 'pending'
    if (info?.doneAt) state = 'done'
    if (info?.error && !info?.doneAt) state = 'error'
    if (key === currentAgent) state = 'current'
    const duration = info?.startedAt && info?.doneAt
      ? new Date(info.doneAt) - new Date(info.startedAt)
      : null
    return { key, label, state, retries: info?.retries || 0, error: info?.error, duration }
  })
}

export default function PipelineTimeline({ job }) {
  const [history, setHistory] = useState(null)
  const { events } = useWs()

  // Histórico persistido (logs/agents.log) — carrega uma vez ao expandir.
  useEffect(() => {
    let alive = true
    api.get(`/system/logs?job_id=${job.id}&limit=400`)
      .then((d) => { if (alive) setHistory(d.events || []) })
      .catch(() => { if (alive) setHistory([]) })
    return () => { alive = false }
  }, [job.id])

  // Eventos WS ao vivo deste job — a timeline anda em tempo real durante o render.
  const live = useMemo(
    () => events.filter((e) => e.job_id === job.id && e.type === 'agent_event'),
    [events, job.id],
  )

  const stages = useMemo(() => {
    if (history === null) return null
    return buildStages([...history, ...live], job)
  }, [history, live, job])

  if (history === null) {
    return <div className="mt-3 space-y-1.5">{Array.from({ length: 6 }).map((_, i) => <div key={i} className="h-7 skeleton rounded-btn" />)}</div>
  }

  const started = stages.some((s) => s.state !== 'pending')
  if (!started && job.status === 'queued') {
    return <p className="mt-3 text-xs" style={{ color: 'var(--text-muted)' }}>Na fila — as etapas aparecem aqui quando a produção começar.</p>
  }

  // Não mostra etapas "pending" depois da última etapa conhecida (jobs antigos
  // sem log completo não fingem um pipeline inteiro pendente).
  let lastKnown = -1
  stages.forEach((s, i) => { if (s.state !== 'pending') lastKnown = i })
  const visible = stages.slice(0, Math.max(lastKnown + 2, 1))

  return (
    <div className="mt-3 rounded-card p-3" style={{ background: 'var(--bg-elevated)', border: '1px solid var(--border)' }}>
      <p className="text-[10px] font-bold uppercase mb-2" style={{ color: 'var(--text-dim)', letterSpacing: '.07em' }}>
        Pipeline do vídeo
      </p>
      <ol className="relative space-y-0.5" style={{ marginLeft: 11 }}>
        {/* Trilho vertical conectando as etapas */}
        <span aria-hidden className="absolute left-0 top-1 bottom-2 w-px" style={{ background: 'var(--border)' }} />
        {visible.map((s) => {
          const meta = STATE_STYLE[s.state]
          return (
            <li key={s.key} className="relative flex items-center gap-2.5 py-1 pl-5 fade-in">
              <span
                aria-hidden
                className={`absolute -left-[11px] w-[11px] h-[11px] rounded-full grid place-items-center text-[7px] font-black ${s.state === 'current' ? 'animate-pulse' : ''}`}
                style={{ background: meta.color, color: 'var(--bg-base)', boxShadow: s.state === 'current' ? `0 0 0 3px color-mix(in srgb, ${meta.color} 25%, transparent)` : 'none' }}
              />
              <span className="text-xs font-semibold w-32 sm:w-40 truncate" style={{ color: s.state === 'pending' ? 'var(--text-dim)' : 'var(--text-primary)' }}>
                {s.label}
              </span>
              <span className="text-[10px] font-semibold" style={{ color: meta.color }}>{meta.label}</span>
              {s.duration != null && (
                <span className="text-[10px] font-mono" style={{ color: 'var(--text-dim)' }}>{fmtDuration(s.duration)}</span>
              )}
              {s.retries > 0 && (
                <span className="badge text-[9px]" style={{ background: 'color-mix(in srgb, var(--warning) 14%, transparent)', color: 'var(--warning)', border: 'none' }}
                  title={`${s.retries} tentativa(s) extra antes de concluir/falhar`}>
                  {s.retries}↻
                </span>
              )}
              {s.error && (
                <span className="text-[10px] truncate" style={{ color: 'var(--error)' }} title={s.error}>
                  {String(s.error).slice(0, 80)}
                </span>
              )}
            </li>
          )
        })}
      </ol>
      {(job.status === 'awaiting_approval' || job.status === 'approved' || job.status === 'published') && (
        <p className="text-[10px] mt-2 pl-5" style={{ color: 'var(--text-muted)' }}>
          {job.status === 'published' ? '🚀 Publicado.' : job.status === 'approved' ? '✓ Aprovado — aguardando publicação.' : '⏸ Aguardando sua aprovação.'}
        </p>
      )}
    </div>
  )
}
