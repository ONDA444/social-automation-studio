import { useState } from 'react'
import { api } from '../api'
import { PLATFORM_META } from '../lib'
import VoiceRecorder from './VoiceRecorder.jsx'

export default function PlatformCard({ account, onChange, onChanged, onDone }) {
  const [recording, setRecording] = useState(false)
  const m = PLATFORM_META[account.platform] || { label: account.platform, color: 'var(--accent)', icon: '●' }
  const quotaPct = account.quota_limit ? Math.round((account.quota_used_today / account.quota_limit) * 100) : 0
  const refresh = onChange || onChanged || onDone
  const connected = account.has_credentials === true

  const connect = async () => {
    try {
      const { auth_url } = await api.get(`/auth/${account.platform}/start?account_id=${account.id}`)
      window.open(auth_url, '_blank', 'width=600,height=720')
    } catch (e) {
      alert('OAuth indisponível: ' + e.message)
    }
  }
  const disconnect = async () => {
    try {
      await api.post(`/accounts/${account.id}/disconnect`)
      refresh?.()
    } catch (e) {
      alert('Falha ao desconectar: ' + e.message)
    }
  }
  const toggle = async () => {
    await api.post(`/accounts/${account.id}/${account.status === 'active' ? 'pause' : 'resume'}`)
    refresh?.()
  }
  const remove = async () => {
    if (confirm('Remover esta conta?')) { await api.del(`/accounts/${account.id}`); refresh?.() }
  }

  const statusColor = { active: 'var(--success)', paused: 'var(--text-muted)', quota_exceeded: 'var(--warning)', auth_error: 'var(--error)' }[account.status] || 'var(--text-muted)'

  return (
    <div className="card p-5" style={{ borderColor: m.color + '40' }}>
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-2">
          <span className="w-9 h-9 rounded-card flex items-center justify-center text-lg" style={{ background: m.color + '22', color: m.color }}>{m.icon}</span>
          <div>
            <p className="font-medium leading-tight">{account.display_name}</p>
            <p className="text-xs text-text-muted">
              {m.label}{account.channel_id ? ` · ${account.channel_id}` : ''}{account.niche ? ` · ${account.niche}` : ''}
            </p>
          </div>
        </div>
        {connected
          ? <span className="badge text-[10px]" style={{ background: 'var(--success)' + '22', color: 'var(--success)' }}>● Conectado</span>
          : <span className="badge text-[10px]" style={{ background: statusColor + '22', color: statusColor }}>{account.status}</span>}
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
        {connected
          ? <button className="btn-ghost flex-1 text-xs" style={{ color: 'var(--error)' }} onClick={disconnect}>⤫ Desconectar</button>
          : <button className="btn-ghost flex-1 text-xs" onClick={connect}>🔗 Conectar</button>}
        <button className="btn-ghost text-xs" onClick={() => setRecording(true)} title="Gravar e clonar minha voz para este canal">🎤</button>
        <button className="btn-ghost text-xs" onClick={toggle} title={account.status === 'active' ? 'Pausar' : 'Retomar'}>{account.status === 'active' ? '⏸' : '▶'}</button>
        <button className="btn-ghost text-xs" onClick={remove} title="Remover conta">🗑</button>
      </div>
      {account.preferred_voice && account.preferred_voice.startsWith('v_') &&
        <p className="text-[10px] text-accent mt-1">🎙️ Voz clonada ativa</p>}
      {recording && <VoiceRecorder account={account} onClose={() => setRecording(false)} onCloned={() => { setRecording(false); refresh?.() }} />}
      {connected
        ? <p className="text-[10px] text-success mt-2">✓ {account.channel_id || account.display_name} conectado</p>
        : <p className="text-[10px] text-text-muted mt-2">Conta não conectada</p>}
    </div>
  )
}
