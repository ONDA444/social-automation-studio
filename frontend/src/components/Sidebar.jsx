import { NavLink } from 'react-router-dom'

const NAV = [
  { to: '/', icon: '📊', label: 'Dashboard' },
  { to: '/queue', icon: '📋', label: 'Fila' },
  { to: '/shorts', icon: '🎞️', label: 'Shorts' },
  { to: '/approvals', icon: '✅', label: 'Aprovações' },
  { to: '/remix', icon: '🔀', label: 'Remix' },
  { to: '/platforms', icon: '📱', label: 'Contas' },
  { to: '/schedule', icon: '🕐', label: 'Agenda' },
  { to: '/analytics', icon: '📈', label: 'Analytics' },
  { to: '/settings', icon: '⚙️', label: 'Config' },
]

export default function Sidebar({ mobileOpen = false, onClose = () => {} }) {
  return (
    <>
      {/* Rail desktop — inalterado em telas md+ */}
      <aside className="hidden md:flex w-20 shrink-0 bg-surface border-r border-border flex-col items-center py-4 gap-2">
        <div className="w-11 h-11 rounded-card bg-accent flex items-center justify-center text-xl mb-4 shadow-[0_4px_18px_rgba(108,92,231,0.4)] transition-transform duration-200 hover:scale-105">
          🎬
        </div>
        {NAV.map((item) => (
          <NavLink
            key={item.to}
            to={item.to}
            end={item.to === '/'}
            title={item.label}
            className={({ isActive }) =>
              `group relative w-14 h-14 rounded-card flex flex-col items-center justify-center gap-0.5 transition-all duration-150 ${
                isActive ? 'bg-accent/20 text-accent' : 'text-text-muted hover:bg-elevated hover:text-text-primary'
              }`
            }
          >
            {({ isActive }) => (
              <>
                <span className={`absolute left-0 top-1/2 -translate-y-1/2 w-1 rounded-r-full bg-accent transition-all duration-200 ${isActive ? 'h-7 opacity-100' : 'h-0 opacity-0'}`} />
                <span className="text-lg leading-none transition-transform duration-150 group-hover:scale-110">{item.icon}</span>
                <span className="text-[9px] font-medium">{item.label}</span>
              </>
            )}
          </NavLink>
        ))}
      </aside>

      {/* Overlay mobile — só aparece abaixo de md quando aberto */}
      <div
        className={`md:hidden fixed inset-0 z-40 bg-black/60 transition-opacity duration-200 ${
          mobileOpen ? 'opacity-100' : 'opacity-0 pointer-events-none'
        }`}
        onClick={onClose}
        aria-hidden="true"
      />

      {/* Drawer mobile — desliza da esquerda */}
      <aside
        className={`md:hidden fixed top-0 left-0 z-50 h-full w-64 bg-surface border-r border-border flex flex-col py-4 px-3 gap-1 shadow-card transform transition-transform duration-200 ${
          mobileOpen ? 'translate-x-0' : '-translate-x-full'
        }`}
        aria-hidden={!mobileOpen}
      >
        <div className="flex items-center justify-between mb-4 px-1">
          <div className="flex items-center gap-2">
            <div className="w-10 h-10 rounded-card bg-accent flex items-center justify-center text-lg shadow-card">
              🎬
            </div>
            <span className="heading text-sm font-semibold">Studio</span>
          </div>
          <button
            onClick={onClose}
            aria-label="Fechar menu"
            className="w-9 h-9 rounded-btn flex items-center justify-center text-text-muted hover:bg-elevated hover:text-text-primary transition-colors"
          >
            ✕
          </button>
        </div>
        {NAV.map((item) => (
          <NavLink
            key={item.to}
            to={item.to}
            end={item.to === '/'}
            onClick={onClose}
            className={({ isActive }) =>
              `flex items-center gap-3 w-full px-3 py-2.5 rounded-card transition-colors ${
                isActive ? 'bg-accent/20 text-accent' : 'text-text-muted hover:bg-elevated hover:text-text-primary'
              }`
            }
          >
            <span className="text-lg leading-none">{item.icon}</span>
            <span className="text-sm font-medium">{item.label}</span>
          </NavLink>
        ))}
      </aside>
    </>
  )
}
