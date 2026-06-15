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

export default function Sidebar() {
  return (
    <aside className="w-20 shrink-0 bg-surface border-r border-border flex flex-col items-center py-4 gap-2">
      <div className="w-11 h-11 rounded-card bg-accent flex items-center justify-center text-xl mb-4 shadow-card">
        🎬
      </div>
      {NAV.map((item) => (
        <NavLink
          key={item.to}
          to={item.to}
          end={item.to === '/'}
          title={item.label}
          className={({ isActive }) =>
            `group relative w-14 h-14 rounded-card flex flex-col items-center justify-center gap-0.5 transition-colors ${
              isActive ? 'bg-accent/20 text-accent' : 'text-text-muted hover:bg-elevated hover:text-text-primary'
            }`
          }
        >
          <span className="text-lg leading-none">{item.icon}</span>
          <span className="text-[9px] font-medium">{item.label}</span>
        </NavLink>
      ))}
    </aside>
  )
}
