import { NavLink } from 'react-router-dom'

const NAV_GROUPS = [
  {
    label: 'Operacao',
    items: [
      { to: '/', icon: 'grid', label: 'Dashboard' },
      { to: '/queue', icon: 'film', label: 'Fila' },
      { to: '/schedule', icon: 'calendar', label: 'Agenda' },
      { to: '/approvals', icon: 'check', label: 'Aprovacoes' },
    ],
  },
  {
    label: 'Conteudo',
    items: [
      { to: '/shorts', icon: 'phone', label: 'Shorts' },
      { to: '/remix', icon: 'spark', label: 'Remix' },
      { to: '/trends', icon: 'chart', label: 'Tendencias' },
      { to: '/media', icon: 'film', label: 'Midia' },
    ],
  },
  {
    label: 'Inteligencia',
    items: [
      { to: '/copilot', icon: 'bot', label: 'AI Copilot' },
      { to: '/rules', icon: 'zap', label: 'Automacoes' },
    ],
  },
  {
    label: 'Canais',
    items: [
      { to: '/channels', icon: 'tv', label: 'Canais' },
      { to: '/platforms', icon: 'link', label: 'Plataformas' },
      { to: '/analytics', icon: 'chart', label: 'Analytics' },
      { to: '/system', icon: 'pulse', label: 'Sistema' },
      { to: '/settings', icon: 'gear', label: 'Config' },
    ],
  },
]

function NavIcon({ name }) {
  const common = { fill: 'none', stroke: 'currentColor', strokeWidth: 1.8, strokeLinecap: 'round', strokeLinejoin: 'round' }
  const paths = {
    grid: <><rect x="3" y="3" width="7" height="7" rx="1.5" /><rect x="14" y="3" width="7" height="7" rx="1.5" /><rect x="3" y="14" width="7" height="7" rx="1.5" /><rect x="14" y="14" width="7" height="7" rx="1.5" /></>,
    film: <><rect x="4" y="5" width="16" height="14" rx="2" /><path d="M8 5v14M16 5v14M4 10h16M4 14h16" /></>,
    calendar: <><rect x="4" y="5" width="16" height="16" rx="2" /><path d="M8 3v4M16 3v4M4 10h16" /></>,
    check: <><path d="M20 6 9 17l-5-5" /></>,
    phone: <><rect x="8" y="3" width="8" height="18" rx="2" /><path d="M11 18h2" /></>,
    spark: <><path d="m12 3 1.7 5.3L19 10l-5.3 1.7L12 17l-1.7-5.3L5 10l5.3-1.7L12 3Z" /><path d="M19 16v4M17 18h4" /></>,
    link: <><path d="M10 13a5 5 0 0 0 7.1 0l2-2a5 5 0 0 0-7.1-7.1l-1.1 1.1" /><path d="M14 11a5 5 0 0 0-7.1 0l-2 2A5 5 0 0 0 12 20.1l1.1-1.1" /></>,
    tv: <><rect x="3" y="7" width="18" height="13" rx="2" /><path d="m8 7 4-4 4 4" /></>,
    chart: <><path d="M4 19V5" /><path d="M4 19h17" /><path d="m7 15 4-4 3 3 5-7" /></>,
    gear: <><circle cx="12" cy="12" r="3" /><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.9l.1.1-2 3-.2-.1a1.7 1.7 0 0 0-1.9-.3 1.7 1.7 0 0 0-1 1.5v.2h-3.4v-.2a1.7 1.7 0 0 0-1-1.5 1.7 1.7 0 0 0-1.9.3l-.2.1-2-3 .1-.1A1.7 1.7 0 0 0 4.6 15a1.7 1.7 0 0 0-1.5-1H3v-3.4h.1a1.7 1.7 0 0 0 1.5-1 1.7 1.7 0 0 0-.3-1.9l-.1-.1 2-3 .2.1a1.7 1.7 0 0 0 1.9.3 1.7 1.7 0 0 0 1-1.5V3h3.4v.2a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.9-.3l.2-.1 2 3-.1.1a1.7 1.7 0 0 0-.3 1.9 1.7 1.7 0 0 0 1.5 1h.1V14h-.1a1.7 1.7 0 0 0-1.5 1Z" /></>,
    bot: <><rect x="4" y="8" width="16" height="11" rx="2.5" /><path d="M12 4v4M9 16h6" /><circle cx="12" cy="3.5" r="1" /><circle cx="8.7" cy="12.5" r="0.6" fill="currentColor" /><circle cx="15.3" cy="12.5" r="0.6" fill="currentColor" /></>,
    pulse: <><path d="M3 12h4l2.5-6 4 12 2.5-6h5" /></>,
    zap: <><path d="M13 2 4.5 13.5H11L10 22l8.5-11.5H13L13 2Z" /></>,
  }
  return <svg width="18" height="18" viewBox="0 0 24 24" {...common}>{paths[name]}</svg>
}

const sidebarBase = {
  background: 'var(--sidebar-base)',
  borderRight: '1px solid rgba(255,255,255,0.08)',
}

function SidebarContent({ onItemClick, showCloseBtn, onClose }) {
  return (
    <>
      <div className="px-4 pt-5 pb-4 shrink-0">
        <div className="flex items-center gap-3">
          <div className="w-10 h-10 rounded-btn flex items-center justify-center border"
            style={{ background: 'var(--grad-accent)', borderColor: 'rgba(255,255,255,0.18)', color: '#fff', boxShadow: '0 4px 14px var(--accent-glow)' }}>
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none">
              <path d="M5 17V7l7-4 7 4v10l-7 4-7-4Z" stroke="currentColor" strokeWidth="1.8" />
              <path d="M8 12h8M12 8v8" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
            </svg>
          </div>
          <div className="min-w-0 flex-1">
            <p className="m-0 text-sm font-bold leading-tight" style={{ color: 'var(--text-inverse)' }}>Social Studio</p>
            <p className="m-0 mt-0.5 text-[11px]" style={{ color: 'rgba(240,241,255,0.60)' }}>producao automatica</p>
          </div>
          {showCloseBtn && (
            <button className="shell-icon-button" onClick={onClose} aria-label="Fechar menu">
              <svg width="14" height="14" viewBox="0 0 14 14" fill="none">
                <path d="M1.5 1.5 12.5 12.5M12.5 1.5 1.5 12.5" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
              </svg>
            </button>
          )}
        </div>
      </div>

      <nav className="flex-1 overflow-y-auto px-3 pb-3">
        {NAV_GROUPS.map((group) => (
          <div key={group.label} className="mb-4">
            <p className="px-3 mb-1.5 text-[10px] font-bold uppercase" style={{ color: 'rgba(240,241,255,0.44)', letterSpacing: '.08em' }}>
              {group.label}
            </p>
            <div className="space-y-1">
              {group.items.map((item) => (
                <NavLink
                  key={item.to}
                  to={item.to}
                  end={item.to === '/'}
                  onClick={onItemClick}
                  className={({ isActive }) => `studio-nav-item ${isActive ? 'is-active' : ''}`}
                >
                  <span className="studio-nav-icon"><NavIcon name={item.icon} /></span>
                  <span className="truncate">{item.label}</span>
                </NavLink>
              ))}
            </div>
          </div>
        ))}
      </nav>

      <div className="p-4 shrink-0" style={{ borderTop: '1px solid rgba(255,255,255,0.08)' }}>
        <div className="rounded-btn px-3 py-2" style={{ background: 'rgba(255,255,255,0.05)', border: '1px solid rgba(255,255,255,0.08)' }}>
          <div className="flex items-center gap-2">
            <span className="w-2 h-2 rounded-full" style={{ background: 'var(--success)' }} />
            <span className="text-xs font-semibold" style={{ color: 'var(--text-inverse)' }}>Sistema ativo</span>
          </div>
          <p className="mt-1 text-[11px]" style={{ color: 'rgba(240,241,255,0.55)' }}>agenda, fila e publicacao</p>
        </div>
      </div>
    </>
  )
}

export default function Sidebar({ mobileOpen = false, onClose = () => {} }) {
  return (
    <>
      <aside className="hidden md:flex w-64 shrink-0 flex-col" style={{ ...sidebarBase, height: '100vh', position: 'sticky', top: 0 }}>
        <SidebarContent />
      </aside>

      <div
        className={`md:hidden fixed inset-0 z-40 transition-opacity duration-200 ${mobileOpen ? 'opacity-100' : 'opacity-0 pointer-events-none'}`}
        style={{ background: 'rgba(17,24,20,0.68)', backdropFilter: 'blur(3px)' }}
        onClick={onClose}
        aria-hidden="true"
      />

      <aside
        className={`md:hidden fixed top-0 left-0 z-50 h-full w-64 flex flex-col shadow-2xl transform transition-transform duration-200 ${mobileOpen ? 'translate-x-0' : '-translate-x-full'}`}
        style={sidebarBase}
        aria-hidden={!mobileOpen}
      >
        <SidebarContent showCloseBtn onClose={onClose} onItemClick={onClose} />
      </aside>
    </>
  )
}
