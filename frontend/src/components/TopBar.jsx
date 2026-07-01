import { useEffect, useState } from 'react'
import { useLocation } from 'react-router-dom'
import { api } from '../api'

const PAGE_TITLES = {
  '/': 'Dashboard',
  '/queue': 'Fila de videos',
  '/schedule': 'Agenda',
  '/approvals': 'Aprovacoes',
  '/shorts': 'Shorts',
  '/remix': 'Remix',
  '/platforms': 'Plataformas',
  '/analytics': 'Analytics',
  '/settings': 'Configuracoes',
}

const topBarStyle = {
  position: 'sticky',
  top: 0,
  zIndex: 40,
  height: 62,
  flexShrink: 0,
  background: 'var(--topbar-base)',
  backdropFilter: 'blur(10px)',
  WebkitBackdropFilter: 'blur(10px)',
  borderBottom: '1px solid var(--border)',
  display: 'flex',
  alignItems: 'center',
  justifyContent: 'space-between',
  padding: '0 20px',
}

function StatusChip({ tone = 'neutral', dot, children }) {
  const tones = {
    ok: ['rgba(31,138,91,0.10)', 'rgba(31,138,91,0.22)', 'var(--success)'],
    warn: ['rgba(217,130,43,0.12)', 'rgba(217,130,43,0.24)', 'var(--warning)'],
    bad: ['rgba(194,65,58,0.12)', 'rgba(194,65,58,0.24)', 'var(--error)'],
    neutral: ['rgba(23,32,26,0.05)', 'var(--border)', 'var(--text-muted)'],
  }
  const [bg, border, color] = tones[tone] || tones.neutral
  return (
    <span className="inline-flex items-center gap-2 px-2.5 py-1.5 rounded-pill border text-xs font-semibold" style={{ background: bg, borderColor: border, color }}>
      {dot && <span className="w-1.5 h-1.5 rounded-full" style={{ background: color }} />}
      {children}
    </span>
  )
}

export default function TopBar({ connected, onMenuClick = () => {} }) {
  const [active, setActive] = useState(0)
  const [health, setHealth] = useState('green')
  const location = useLocation()

  useEffect(() => {
    let t
    const poll = async () => {
      try {
        // Fire both in parallel — the old sequential await chained two round-trips
        // every 8s, doubling the status-bar latency.
        const [d, h] = await Promise.all([
          api.get('/dashboard'),
          api.get('/dashboard/health'),
        ])
        setActive(d.active_jobs || 0)
        setHealth(h.status)
      } catch {
        setHealth('red')
      }
      t = setTimeout(poll, 8000)
    }
    poll()
    return () => clearTimeout(t)
  }, [])

  const pageTitle = PAGE_TITLES[location.pathname] || 'Social Studio'
  const healthTone = health === 'green' ? 'ok' : health === 'yellow' ? 'warn' : 'bad'

  return (
    <header style={topBarStyle}>
      <div className="flex items-center gap-3 min-w-0">
        <button
          onClick={onMenuClick}
          aria-label="Abrir menu"
          className="md:hidden btn btn-ghost btn-sm px-2"
        >
          <svg width="20" height="14" viewBox="0 0 20 14" fill="none">
            <path d="M0 1H20M0 7H14M0 13H20" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
          </svg>
        </button>
        <div className="min-w-0">
          <p className="heading text-base sm:text-lg font-extrabold truncate" style={{ color: 'var(--text-primary)' }}>{pageTitle}</p>
          <p className="hidden sm:block text-[11px]" style={{ color: 'var(--text-muted)' }}>Mesa de producao dos seus canais</p>
        </div>
      </div>

      <div className="flex items-center gap-2">
        <StatusChip tone={healthTone} dot>
          <span className="hidden sm:inline">API</span>
          <span className="sm:hidden">API</span>
        </StatusChip>
        <StatusChip tone={active > 0 ? 'warn' : 'neutral'} dot={active > 0}>
          <span className="hidden sm:inline">{active} ativo{active === 1 ? '' : 's'}</span>
          <span className="sm:hidden">{active}</span>
        </StatusChip>
        <StatusChip tone={connected ? 'ok' : 'bad'} dot>
          {connected ? 'live' : 'offline'}
        </StatusChip>
      </div>
    </header>
  )
}
