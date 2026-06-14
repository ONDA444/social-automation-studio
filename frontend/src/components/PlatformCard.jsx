import { api } from '../api'
import { PLATFORM_META } from '../lib'

export default function PlatformCard({ account, onChange }) {
  const m = PLATFORM_META[account.platform] || { label: account.platform, color: 'var(--accent)', icon: '●' }
  const quotaPct = account.quota_limit ? Math.round((account.quota_used_today / account.quota_limit) * 100) : 0

  const connect = async () => {
    try {
      const { auth_url } = await api.get(`/auth/${account.platform}/start?account_id=${account.id}`)
      window.open(auth_url, '_blank', 'width=600,height=720')
    } catch (e) {
      alert('OAuth indisponível: ' + e.message)
    }
  }
  const toggle = async () => {
    await api.post(`/accounts/${account.id}/${account.status === 'active' ? 'pause' : 'resume'}`)
    onChange?.()
  }
  const remove = async () => {
    if (confirm(`Remover ${account.display_name}?`)) { await api.del(`/accounts/${account.id}`); onChange?.() }
  }

  const statusColor = { active: 'var(--success)', paused: 'var(--text-muted)', quota_exceeded: 'var(--warning)', auth_error: 'var(--error)' }[account.status] || 'var(--text-muted)'

  return (
    <div className="card p-5" style={{ borderColor: m.color + '40' }}>
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-2">
          <span className="w-9 h-9 rounded-card flex items-center justify-center text-lg" style={{ background: m.color + '22', color: m.color }}>{m.icon}</span>
          <div>
            <p className="font-medium leading-tight">{account.display_name}</p>
            <p className="text-xs text-text-muted">{m.label}{account.niche ? ` · ${account.niche}` : ''}</p>
          </div>
        </div>
        <span className="badge text-[10px]" style={{ background: statusColor + '22', color: statusColor }}>{account.status}</span>
      </div>

      <div className="mb-3">
        <div className="flex justify-between text-[11px] text-text-muted mb-1">
          <span>Quota diária</span><span>{account.quota_used_today}/{account.quota_limit}</span>
        </div>
        <div className="h-1.5 rounded-full bg-elevated overflow-hidden">
          <div className="h-full" style={{ width: `${quotaPct}%`, background: quotaPct > 90 ? 'var(--error)' : m.color }} />
        </div>
      </div>

      <div className="flex items-center gap-2">
        <button className="btn-ghost flex-1 text-xs" onClick={connect}>
          {account.has_credentials ? '↻ Reconectar' : '🔗 Conectar'}
        </button>
        <button className="btn-ghost text-xs" onClick={toggle}>{account.status === 'active' ? '⏸' : '▶'}</button>
        <button className="btn-ghost text-xs" onClick={remove}>🗑</button>
      </div>
      {account.has_credentials && <p className="text-[10px] text-success mt-2">✓ conta conectada</p>}
    </div>
  )
}
