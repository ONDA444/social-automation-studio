import { useEffect, useState } from 'react'
import { api } from '../api'

export default function TopBar({ connected }) {
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
      <div>
        <h1 className="heading text-lg font-semibold">Social Automation Studio</h1>
        <p className="text-xs text-text-muted">Geração e publicação de vídeos por IA</p>
      </div>
      <div className="flex items-center gap-4 text-sm">
        <span className="badge bg-elevated text-text-muted">
          <span className="w-2 h-2 rounded-full" style={{ background: dot }} /> sistema
        </span>
        <span className="badge bg-elevated text-text-muted">
          ● {active} job{active === 1 ? '' : 's'} ativo{active === 1 ? '' : 's'}
        </span>
        <span className="badge" style={{ background: connected ? 'rgba(0,214,143,.15)' : 'rgba(255,71,87,.15)', color: connected ? 'var(--success)' : 'var(--error)' }}>
          {connected ? '🔴 live' : '○ offline'}
        </span>
      </div>
    </header>
  )
}
