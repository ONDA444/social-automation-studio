import { useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useWs, useDashboardStatus } from '../App.jsx'

// Central de Notificações — alimentada pelos MESMOS eventos WebSocket reais do
// pipeline (nada simulado): mudanças de status de job e erros/retries de agentes
// viram notificações categorizadas (success | error | alert | info | action).
// Persistida em localStorage (cap 50) para sobreviver a reloads; o badge conta
// as não lidas desde o último "marcar como lidas".

const LS_KEY = 'studio-notifications'
const LS_READ_KEY = 'studio-notifications-read-at'
const MAX_ITEMS = 50

const CATEGORY_META = {
  success: { icon: '✓', color: 'var(--success)' },
  error:   { icon: '✕', color: 'var(--error)' },
  alert:   { icon: '!', color: 'var(--warning)' },
  action:  { icon: '→', color: 'var(--accent-blue)' },
  info:    { icon: 'ℹ', color: 'var(--text-muted)' },
}

function loadItems() {
  try { return JSON.parse(localStorage.getItem(LS_KEY)) || [] } catch { return [] }
}

function readAt() {
  try { return Number(localStorage.getItem(LS_READ_KEY)) || 0 } catch { return 0 }
}

// Map a raw WS event to a notification (or null when it's not noteworthy —
// progress spam and routine agent starts stay out of the bell).
function eventToNotification(ev) {
  const now = ev._t || Date.now()
  // Evento direto da engine de automações (§27) — já vem pronto para o sino.
  if (ev.type === 'notification' && ev.message) {
    return {
      id: `rule:${ev.rule || ''}:${ev.job_id ?? ''}:${now}`,
      at: now,
      category: ev.category || 'info',
      message: ev.message,
      to: ev.to || null,
    }
  }
  if (ev.type === 'job_update') {
    const map = {
      awaiting_approval: { category: 'action', message: `Vídeo #${ev.job_id} finalizado e aguardando aprovação.`, to: '/approvals' },
      error:             { category: 'error',  message: `Job #${ev.job_id} falhou${ev.error ? `: ${String(ev.error).slice(0, 120)}` : '.'}`, to: '/queue' },
      published:         { category: 'success', message: `Vídeo #${ev.job_id} publicado com sucesso.`, to: '/queue' },
      approved:          { category: 'info',   message: `Vídeo #${ev.job_id} aprovado — entrará na fila de publicação.`, to: '/queue' },
      voice_fallback:    { category: 'alert',  message: `Job #${ev.job_id} publicado com voz em fallback (${ev.tts_provider || 'TTS alternativo'}).`, to: '/queue' },
    }
    const hit = map[ev.status]
    if (hit) return { id: `job:${ev.job_id}:${ev.status}`, at: now, ...hit }
  }
  if (ev.type === 'agent_event' && ev.status === 'error') {
    return {
      id: `agent:${ev.job_id}:${ev.agent}:${now}`,
      at: now,
      category: 'error',
      message: `Agente ${ev.agent} esgotou as tentativas${ev.job_id != null ? ` (job #${ev.job_id})` : ''}.`,
      to: '/queue',
    }
  }
  return null
}

function relTime(ts) {
  const s = Math.max(0, Math.floor((Date.now() - ts) / 1000))
  if (s < 60) return 'agora'
  const m = Math.floor(s / 60)
  if (m < 60) return `há ${m} min`
  const h = Math.floor(m / 60)
  if (h < 24) return `há ${h} h`
  return `há ${Math.floor(h / 24)} d`
}

export default function NotificationBell() {
  const ws = useWs()
  const { data } = useDashboardStatus()
  const [items, setItems] = useState(loadItems)
  const [open, setOpen] = useState(false)
  const [readMark, setReadMark] = useState(readAt)
  const lastCountRef = useRef(0)
  const seededRef = useRef(false)
  const nav = useNavigate()
  const wrapRef = useRef(null)

  // Consume only the NEW events since the last flush (ws.count is monotonic).
  useEffect(() => {
    const prev = lastCountRef.current
    lastCountRef.current = ws.count
    const delta = ws.count - prev
    if (delta <= 0) return
    const fresh = ws.events.slice(-delta)
    const mapped = fresh.map(eventToNotification).filter(Boolean)
    if (!mapped.length) return
    setItems((cur) => {
      const seen = new Set(cur.map((i) => i.id))
      const next = [...mapped.filter((i) => !seen.has(i.id)), ...cur]
      const capped = next.slice(0, MAX_ITEMS)
      try { localStorage.setItem(LS_KEY, JSON.stringify(capped)) } catch { /* quota */ }
      return capped
    })
  }, [ws.count, ws.events])

  // Seed: pending approvals that predate this session are actionable too —
  // surface them once so a reload doesn't hide work waiting on the operator.
  useEffect(() => {
    if (seededRef.current) return
    const awaiting = data?.status_counts?.awaiting_approval || 0
    if (!awaiting) return
    seededRef.current = true
    const id = 'seed:approvals'
    setItems((cur) => {
      if (cur.some((i) => i.id === id)) return cur
      const next = [{
        id, at: Date.now(), category: 'action', to: '/approvals',
        message: `${awaiting} vídeo(s) aguardando aprovação.`,
      }, ...cur].slice(0, MAX_ITEMS)
      try { localStorage.setItem(LS_KEY, JSON.stringify(next)) } catch { /* quota */ }
      return next
    })
  }, [data])

  // Close on outside click / Esc.
  useEffect(() => {
    if (!open) return
    const onDown = (e) => {
      if (wrapRef.current && !wrapRef.current.contains(e.target)) { markAllRead(); setOpen(false) }
    }
    const onKey = (e) => { if (e.key === 'Escape') { markAllRead(); setOpen(false) } }
    document.addEventListener('mousedown', onDown)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', onDown)
      document.removeEventListener('keydown', onKey)
    }
  }, [open])

  const unread = useMemo(() => items.filter((i) => i.at > readMark).length, [items, readMark])

  const markAllRead = () => {
    const now = Date.now()
    setReadMark(now)
    try { localStorage.setItem(LS_READ_KEY, String(now)) } catch { /* quota */ }
  }

  const openItem = (item) => {
    setOpen(false)
    markAllRead()
    if (item.to) nav(item.to)
  }

  return (
    <div className="relative" ref={wrapRef}>
      <button
        type="button"
        className="btn-ghost btn-sm relative px-2"
        onClick={() => { if (open) markAllRead(); setOpen((v) => !v) }}
        aria-label={unread ? `${unread} notificações não lidas` : 'Notificações'}
        aria-expanded={open}
      >
        <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor"
          strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
          <path d="M18 8a6 6 0 0 0-12 0c0 7-3 9-3 9h18s-3-2-3-9" />
          <path d="M13.7 21a2 2 0 0 1-3.4 0" />
        </svg>
        {unread > 0 && (
          <span className="absolute -top-1 -right-1 min-w-[16px] h-4 px-1 rounded-pill grid place-items-center text-[10px] font-bold"
            style={{ background: 'var(--error)', color: '#fff' }}>
            {unread > 9 ? '9+' : unread}
          </span>
        )}
      </button>

      {open && (
        <div className="absolute right-0 top-full mt-2 w-[min(92vw,360px)] rounded-card shadow-lg overflow-hidden slide-down z-50"
          style={{ background: 'var(--bg-surface)', border: '1px solid var(--border)' }}
          role="dialog" aria-label="Central de notificações">
          <div className="flex items-center justify-between px-4 py-2.5"
            style={{ borderBottom: '1px solid var(--border)' }}>
            <p className="text-sm font-bold" style={{ color: 'var(--text-primary)' }}>Notificações</p>
            {items.length > 0 && (
              <button className="text-[11px] font-semibold" style={{ color: 'var(--accent)' }}
                onClick={() => { setItems([]); try { localStorage.removeItem(LS_KEY) } catch { /* */ } }}>
                Limpar
              </button>
            )}
          </div>
          <div className="max-h-[380px] overflow-y-auto">
            {items.length === 0 ? (
              <p className="text-xs text-center py-8" style={{ color: 'var(--text-muted)' }}>
                Nenhuma notificação ainda. Eventos do pipeline aparecem aqui em tempo real.
              </p>
            ) : items.map((item) => {
              const meta = CATEGORY_META[item.category] || CATEGORY_META.info
              const isUnread = item.at > readMark
              return (
                <button key={item.id} type="button" onClick={() => openItem(item)}
                  className="w-full text-left flex items-start gap-2.5 px-4 py-3 transition-colors"
                  style={{ background: isUnread ? 'var(--accent-dim)' : 'transparent', borderBottom: '1px solid var(--border)' }}>
                  <span aria-hidden className="shrink-0 w-6 h-6 rounded-full grid place-items-center text-[11px] font-bold mt-0.5"
                    style={{ background: `color-mix(in srgb, ${meta.color} 16%, transparent)`, color: meta.color }}>
                    {meta.icon}
                  </span>
                  <span className="flex-1 min-w-0">
                    <span className="block text-xs leading-snug" style={{ color: 'var(--text-primary)' }}>{item.message}</span>
                    <span className="block text-[10px] mt-0.5" style={{ color: 'var(--text-dim)' }}>{relTime(item.at)}</span>
                  </span>
                  {isUnread && <span className="w-1.5 h-1.5 rounded-full mt-2 shrink-0" style={{ background: 'var(--accent)' }} />}
                </button>
              )
            })}
          </div>
        </div>
      )}
    </div>
  )
}
