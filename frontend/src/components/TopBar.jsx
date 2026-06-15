import { useEffect, useState } from 'react'
import { api } from '../api'

export default function TopBar({ connected, onMenuClick = () => {} }) {
  const [active, setActive] = useState(0)
  const [health, setHealth] = useState('green')

  useEffect(() => {
    let t
    const poll = async () => {
      try {
        const d = await api.get('/dashboard')
        setActive(d.active_jobs || 0)
        const h = await api.get('/dashboard/health')
        setHealth(h.status)
      } catch { /* backend offline */ setHealth('red') }
      t = setTimeout(poll, 8000)
    }
    poll()
    return () => clearTimeout(t)
  }, [])

  const dot = { green: 'var(--success)', yellow: 'var(--warning)', red: 'var(--error)' }[health]

  return (
    <header className="h-15 shrink-0 border-b border-border bg-surface/60 backdrop-blur px-6 py-3 flex items-center justify-between">
      <div className="flex items-center gap-3 min-w-0">
        <button
          onClick={onMenuClick}
          aria-label="Abrir menu"
          className="md:hidden w-10 h-10 shrink-0 rounded-btn flex items-center justify-center text-xl text-text-primary hover:bg-elevated transition-colors"
        >
          ☰
        </button>
        <div className="min-w-0">
          <h1 className="heading text-lg font-semibold truncate">Social Automation Studio</h1>
          <p className="text-xs text-text-muted truncate hidden sm:block">Geração e publicação de vídeos por IA</p>
        </div>
      </div>
      <div className="flex items-center gap-2 sm:gap-3 text-sm">
        <span className="badge bg-elevated text-text-muted">
          <span className="relative flex w-2 h-2">
            {health === 'green' && <span className="absolute inline-flex w-full h-full rounded-full opacity-60 animate-ping" style={{ background: dot }} />}
            <span className="relative inline-flex w-2 h-2 rounded-full" style={{ background: dot }} />
          </span>
          <span className="hidden sm:inline">sistema</span>
        </span>
        <span className="badge bg-elevated text-text-muted">
          <span className="w-1.5 h-1.5 rounded-full" style={{ background: active > 0 ? 'var(--accent)' : 'var(--text-muted)' }} /> {active} job{active === 1 ? '' : 's'} ativo{active === 1 ? '' : 's'}
        </span>
        <span className="badge transition-colors" style={{ background: connected ? 'rgba(0,214,143,.15)' : 'rgba(255,71,87,.15)', color: connected ? 'var(--success)' : 'var(--error)' }}>
          {connected ? '🔴 live' : '○ offline'}
        </span>
      </div>
    </header>
  )
}
