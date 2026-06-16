import { NavLink } from 'react-router-dom'

const NAV = [
  { to: '/', icon: '📊', label: 'Dashboard' },
  { to: '/queue', icon: '🎬', label: 'Fila' },
  { to: '/schedule', icon: '📅', label: 'Agenda' },
  { to: '/approvals', icon: '✅', label: 'Aprovações' },
  { to: '/shorts', icon: '📱', label: 'Shorts' },
  { to: '/remix', icon: '🎨', label: 'Remix' },
  { to: '/platforms', icon: '🔗', label: 'Plataformas' },
  { to: '/analytics', icon: '📈', label: 'Analytics' },
  { to: '/settings', icon: '⚙️', label: 'Config' },
]

const sidebarBase = {
  background: 'rgba(6,6,12,0.95)',
  backdropFilter: 'blur(24px)',
  WebkitBackdropFilter: 'blur(24px)',
  borderRight: '1px solid rgba(124,106,255,0.15)',
}

function SidebarContent({ onItemClick, showCloseBtn, onClose }) {
  return (
    <>
      {/* ── Logo area ── */}
      <div style={{ padding: '22px 16px 20px', position: 'relative', display: 'flex', alignItems: 'center', gap: 12, flexShrink: 0 }}>
        {/* Glow orb */}
        <div
          aria-hidden="true"
          style={{
            position: 'absolute',
            left: -8,
            top: '50%',
            transform: 'translateY(-50%)',
            width: 90,
            height: 90,
            background: 'radial-gradient(ellipse at center, rgba(124,106,255,0.38) 0%, transparent 70%)',
            filter: 'blur(18px)',
            pointerEvents: 'none',
          }}
        />
        {/* SAS badge */}
        <div
          style={{
            position: 'relative',
            zIndex: 1,
            width: 40,
            height: 40,
            borderRadius: 12,
            background: 'linear-gradient(135deg, #7C6AFF 0%, #A78BFA 100%)',
            boxShadow: '0 0 20px rgba(124,106,255,0.5), 0 4px 12px rgba(0,0,0,0.4)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            flexShrink: 0,
          }}
        >
          <span style={{ fontWeight: 900, color: '#fff', fontSize: 12, letterSpacing: '-0.5px' }}>SAS</span>
        </div>
        {/* Name */}
        <div style={{ position: 'relative', zIndex: 1, minWidth: 0, flex: 1 }}>
          <p style={{ margin: 0, fontWeight: 700, color: '#EEEEFF', fontSize: 13, letterSpacing: '-0.2px', lineHeight: 1.2 }}>
            Social Automation
          </p>
          <p style={{ margin: '2px 0 0', fontSize: 11, color: 'rgba(160,150,220,0.65)', lineHeight: 1.2 }}>
            Studio
          </p>
        </div>
        {/* Mobile close button */}
        {showCloseBtn && (
          <button
            onClick={onClose}
            aria-label="Fechar menu"
            style={{
              position: 'relative',
              zIndex: 1,
              width: 30,
              height: 30,
              borderRadius: 8,
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              color: 'rgba(160,150,220,0.7)',
              background: 'transparent',
              border: 'none',
              cursor: 'pointer',
              flexShrink: 0,
              transition: 'background 150ms',
            }}
            onMouseEnter={(e) => { e.currentTarget.style.background = 'rgba(255,255,255,0.07)' }}
            onMouseLeave={(e) => { e.currentTarget.style.background = 'transparent' }}
          >
            <svg width="13" height="13" viewBox="0 0 13 13" fill="none">
              <path d="M1 1L12 12M12 1L1 12" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
            </svg>
          </button>
        )}
      </div>

      {/* ── Nav items ── */}
      <nav style={{ display: 'flex', flexDirection: 'column', gap: 2, padding: '0 12px', flex: 1, overflowY: 'auto' }}>
        {NAV.map((item) => (
          <NavLink
            key={item.to}
            to={item.to}
            end={item.to === '/'}
            onClick={onItemClick}
            className={({ isActive }) =>
              `flex items-center gap-3 px-3 py-2.5 rounded-xl text-sm font-medium border transition-all ${
                isActive ? 'border-accent/40' : 'border-transparent hover:bg-white/5'
              }`
            }
            style={({ isActive }) =>
              isActive
                ? {
                    background: 'rgba(124,106,255,0.18)',
                    color: '#A78BFA',
                    boxShadow: '0 0 14px rgba(124,106,255,0.10)',
                    transitionDuration: '180ms',
                  }
                : {
                    color: '#7070A0',
                    transitionDuration: '180ms',
                  }
            }
          >
            <span
              style={{
                width: 28,
                height: 28,
                borderRadius: 8,
                background: 'rgba(255,255,255,0.04)',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                fontSize: 16,
                lineHeight: 1,
                flexShrink: 0,
              }}
            >
              {item.icon}
            </span>
            <span>{item.label}</span>
          </NavLink>
        ))}
      </nav>

      {/* ── Status footer ── */}
      <div
        style={{
          padding: '14px 16px 20px',
          borderTop: '1px solid rgba(124,106,255,0.10)',
          flexShrink: 0,
        }}
      >
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 8,
            padding: '7px 12px',
            borderRadius: 10,
            background: 'rgba(0,214,143,0.06)',
            border: '1px solid rgba(0,214,143,0.15)',
          }}
        >
          <span
            style={{
              width: 7,
              height: 7,
              borderRadius: '50%',
              background: '#00D68F',
              boxShadow: '0 0 7px #00D68F',
              flexShrink: 0,
              animation: 'sas-pulse 2.2s ease-in-out infinite',
            }}
          />
          <span style={{ fontSize: 12, color: 'rgba(0,214,143,0.85)', fontWeight: 500 }}>
            Railway live
          </span>
        </div>
      </div>
    </>
  )
}

export default function Sidebar({ mobileOpen = false, onClose = () => {} }) {
  return (
    <>
      {/* Keyframe for status dot */}
      <style>{`
        @keyframes sas-pulse {
          0%, 100% { box-shadow: 0 0 5px #00D68F; opacity: 1; }
          50% { box-shadow: 0 0 13px #00D68F; opacity: 0.65; }
        }
      `}</style>

      {/* ── Desktop sidebar ── */}
      <aside
        className="hidden md:flex w-64 shrink-0 flex-col"
        style={{ ...sidebarBase, height: '100vh', position: 'sticky', top: 0 }}
      >
        <SidebarContent />
      </aside>

      {/* ── Mobile: backdrop ── */}
      <div
        className={`md:hidden fixed inset-0 z-40 transition-opacity duration-200 ${
          mobileOpen ? 'opacity-100' : 'opacity-0 pointer-events-none'
        }`}
        style={{ background: 'rgba(0,0,0,0.72)', backdropFilter: 'blur(4px)' }}
        onClick={onClose}
        aria-hidden="true"
      />

      {/* ── Mobile: drawer ── */}
      <aside
        className={`md:hidden fixed top-0 left-0 z-50 h-full w-64 flex flex-col shadow-2xl transform transition-transform duration-200 ${
          mobileOpen ? 'translate-x-0' : '-translate-x-full'
        }`}
        style={sidebarBase}
        aria-hidden={!mobileOpen}
      >
        <SidebarContent showCloseBtn onClose={onClose} onItemClick={onClose} />
      </aside>
    </>
  )
}
