import { useEffect, useRef } from 'react'
import { useWs } from '../App.jsx'

const STATUS_COLOR = {
  started: 'var(--accent)',
  progress: 'var(--text-muted)',
  completed: 'var(--success)',
  retry: 'var(--warning)',
  error: 'var(--error)',
}

export default function AgentLog() {
  const { events, connected } = useWs()
  const scrollRef = useRef(null)
  // Whether to keep pinned to the newest line. Starts true (show latest), flips
  // off the moment the user scrolls up to read history, back on when they return
  // to the bottom — so a new event never yanks them away mid-read.
  const stickRef = useRef(true)

  const log = events.filter((e) => e.type === 'agent_event' || e.type === 'job_update')

  const onScroll = () => {
    const el = scrollRef.current
    if (!el) return
    stickRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < 48
  }

  // Auto-follow only when pinned. Sets scrollTop on the LOG container itself —
  // never scrollIntoView(), which would also scroll the page <main> and make the
  // whole dashboard jump on every event.
  useEffect(() => {
    const el = scrollRef.current
    if (el && stickRef.current) el.scrollTop = el.scrollHeight
  }, [log.length])

  return (
    <div className="card p-4 flex flex-col h-full min-h-0">
      <div className="flex items-center justify-between mb-3">
        <h3 className="heading text-sm font-semibold">AgentLog</h3>
        <span className="badge text-[10px]" style={{ background: connected ? 'rgba(0,214,143,.15)' : 'rgba(136,136,170,.15)', color: connected ? 'var(--success)' : 'var(--text-muted)' }}>
          {connected ? 'ao vivo' : 'desconectado'}
        </span>
      </div>
      <div ref={scrollRef} onScroll={onScroll} className="flex-1 overflow-y-auto font-mono text-[11px] space-y-1 pr-1">
        {log.length === 0 && <p className="text-text-muted">Sem eventos ainda. Crie um job para ver o pipeline ao vivo.</p>}
        {log.map((e, i) => (
          <div key={i} className="flex gap-2 items-baseline">
            <span className="text-text-muted shrink-0">{e.job_id != null ? `#${e.job_id}` : '—'}</span>
            <span className="shrink-0 font-medium" style={{ color: STATUS_COLOR[e.status] || 'var(--text-muted)' }}>
              {e.agent || e.status}
            </span>
            <span className="text-text-primary/80 truncate">{e.message || e.status}</span>
          </div>
        ))}
      </div>
    </div>
  )
}
