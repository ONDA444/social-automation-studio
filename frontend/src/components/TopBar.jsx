import { useEffect, useState } from 'react'
import { api } from '../api'

const topBarStyle = {
  position: 'sticky',
  top: 0,
  zIndex: 40,
  height: 60,
  flexShrink: 0,
  background: 'rgba(6,6,12,0.85)',
  backdropFilter: 'blur(20px)',
  WebkitBackdropFilter: 'blur(20px)',
  borderBottom: '1px solid rgba(124,106,255,0.10)',
  display: 'flex',
  alignItems: 'center',
  justifyContent: 'space-between',
  padding: '0 20px',
}

const badgeBase = {
  display: 'inline-flex',
  alignItems: 'center',
  gap: 6,
  padding: '5px 10px',
  borderRadius: 20,
  fontSize: 12,
  fontWeight: 500,
  border: '1px solid transparent',
  lineHeight: 1,
}

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

  const healthColor = { green: '#00D68F', yellow: '#F59E0B', red: '#FF4757' }[health] || '#00D68F'

  return (
    <header style={topBarStyle}>
      {/* ── Left: menu button + title (mobile) ── */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, minWidth: 0 }}>
        {/* Hamburger — mobile only */}
        <button
          onClick={onMenuClick}
          aria-label="Abrir menu"
          className="md:hidden"
          style={{
            width: 40,
            height: 40,
            borderRadius: 10,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            background: 'transparent',
            border: 'none',
            cursor: 'pointer',
            color: '#EEEEFF',
            flexShrink: 0,
            transition: 'background 150ms',
          }}
          onMouseEnter={(e) => { e.currentTarget.style.background = 'rgba(255,255,255,0.07)' }}
          onMouseLeave={(e) => { e.currentTarget.style.background = 'transparent' }}
        >
          {/* Custom hamburger SVG */}
          <svg width="20" height="14" viewBox="0 0 20 14" fill="none">
            <path d="M0 1H20M0 7H14M0 13H20" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
          </svg>
        </button>

        {/* Title — show on mobile only (desktop sidebar has it) */}
        <div className="md:hidden min-w-0">
          <span style={{ fontWeight: 700, color: '#EEEEFF', fontSize: 15, letterSpacing: '-0.3px' }}>
            Social Automation Studio
          </span>
        </div>
      </div>

      {/* ── Right: status badges ── */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        {/* System health */}
        <span
          style={{
            ...badgeBase,
            background: `${healthColor}12`,
            borderColor: `${healthColor}25`,
            color: healthColor,
          }}
        >
          <span style={{ position: 'relative', display: 'inline-flex', width: 8, height: 8, flexShrink: 0 }}>
            {health === 'green' && (
              <span
                style={{
                  position: 'absolute',
                  inset: 0,
                  borderRadius: '50%',
                  background: healthColor,
                  opacity: 0.5,
                  animation: 'topbar-ping 1.4s ease-out infinite',
                }}
              />
            )}
            <span
              style={{
                position: 'relative',
                width: 8,
                height: 8,
                borderRadius: '50%',
                background: healthColor,
                display: 'inline-flex',
              }}
            />
          </span>
          <span className="hidden sm:inline">sistema</span>
        </span>

        {/* Active jobs */}
        <span
          style={{
            ...badgeBase,
            background: active > 0 ? 'rgba(124,106,255,0.12)' : 'rgba(255,255,255,0.05)',
            borderColor: active > 0 ? 'rgba(124,106,255,0.28)' : 'rgba(255,255,255,0.08)',
            color: active > 0 ? '#A78BFA' : '#7070A0',
          }}
        >
          <span
            style={{
              width: 6,
              height: 6,
              borderRadius: '50%',
              background: active > 0 ? '#7C6AFF' : '#7070A0',
              flexShrink: 0,
            }}
          />
          <span className="hidden sm:inline">
            {active} job{active === 1 ? '' : 's'} ativo{active === 1 ? '' : 's'}
          </span>
          <span className="sm:hidden">{active}</span>
        </span>

        {/* WebSocket connection */}
        <span
          style={{
            ...badgeBase,
            background: connected ? 'rgba(0,214,143,0.12)' : 'rgba(255,71,87,0.12)',
            borderColor: connected ? 'rgba(0,214,143,0.28)' : 'rgba(255,71,87,0.28)',
            color: connected ? '#00D68F' : '#FF4757',
          }}
        >
          <span
            style={{
              width: 7,
              height: 7,
              borderRadius: '50%',
              background: connected ? '#00D68F' : '#FF4757',
              boxShadow: connected ? '0 0 7px #00D68F' : 'none',
              animation: connected ? 'sas-pulse 2.2s ease-in-out infinite' : 'none',
              flexShrink: 0,
            }}
          />
          {connected ? 'live' : 'offline'}
        </span>
      </div>

      <style>{`
        @keyframes topbar-ping {
          0% { transform: scale(1); opacity: 0.5; }
          75%, 100% { transform: scale(2.2); opacity: 0; }
        }
        @keyframes sas-pulse {
          0%, 100% { box-shadow: 0 0 5px #00D68F; opacity: 1; }
          50% { box-shadow: 0 0 13px #00D68F; opacity: 0.65; }
        }
      `}</style>
    </header>
  )
}
