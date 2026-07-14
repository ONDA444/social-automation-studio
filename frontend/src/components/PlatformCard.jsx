import { useState } from 'react'
import { api } from '../api'
import { PLATFORM_META, LANGUAGES, voicesForLang } from '../lib'
import VoiceRecorder from './VoiceRecorder.jsx'
import ChannelOptimizer from './ChannelOptimizer.jsx'

const UPLOAD_COST = { youtube: 1600, tiktok: 1, instagram: 1 }

const STATUS_LABEL = {
  active: 'Ativo',
  paused: 'Pausado',
  quota_exceeded: 'Aguardando quota',
  auth_error: 'Reconectar',
  disconnected: 'Desconectado',
}

function remainingUploads(account) {
  const limit = Number(account.quota_limit || 0)
  const used = Number(account.quota_used_today || 0)
  const cost = UPLOAD_COST[account.platform] || 1
  if (!limit || !cost) return null
  return Math.max(0, Math.floor((limit - used) / cost))
}

export default function PlatformCard({ account, onChange, onChanged, onDone }) {
  const [recording, setRecording] = useState(false)
  const [optimizing, setOptimizing] = useState(false)
  const [expanded, setExpanded] = useState(false)
  const [lang, setLang] = useState(account.content_language || 'pt-BR')
  const [savingLang, setSavingLang] = useState(false)
  const isClone = account.preferred_voice?.startsWith('v_')
  const [voice, setVoice] = useState(isClone ? '' : (account.preferred_voice || ''))
  const [savingVoice, setSavingVoice] = useState(false)
  const [music, setMusic] = useState(account.music_style || 'balanced')
  const [savingMusic, setSavingMusic] = useState(false)
  const [ride, setRide] = useState(account.ride_trends || false)
  const [savingRide, setSavingRide] = useState(false)
  const [toggling, setToggling] = useState(false)
  const [removing, setRemoving] = useState(false)

  const m = PLATFORM_META[account.platform] || { label: account.platform, color: 'var(--accent)', icon: 'CH' }
  const quotaPct = account.quota_limit ? Math.min(100, Math.round((account.quota_used_today / account.quota_limit) * 100)) : 0
  const uploadsLeft = remainingUploads(account)
  const connected = account.has_credentials === true
  const refresh = onChange || onChanged || onDone
  const blocked = account.status === 'quota_exceeded' || uploadsLeft === 0
  const statusColor = {
    active: 'var(--success)',
    paused: 'var(--text-muted)',
    quota_exceeded: 'var(--warning)',
    auth_error: 'var(--error)',
    disconnected: 'var(--text-muted)',
  }[account.status] || 'var(--text-muted)'

  const connect = async () => {
    try {
      const { auth_url } = await api.get(`/auth/${account.platform}/start?account_id=${account.id}`)
      window.open(auth_url, '_blank', 'width=600,height=720')
    } catch (e) {
      alert('OAuth indisponivel: ' + e.message)
    }
  }

  const disconnect = async () => {
    try { await api.post(`/accounts/${account.id}/disconnect`); refresh?.() }
    catch (e) { alert('Falha ao desconectar: ' + e.message) }
  }

  const toggle = async () => {
    setToggling(true)
    try {
      await api.post(`/accounts/${account.id}/${account.status === 'active' ? 'pause' : 'resume'}`)
      refresh?.()
    } catch (e) {
      alert('Falha ao atualizar status: ' + e.message)
    } finally {
      setToggling(false)
    }
  }

  const remove = async () => {
    if (confirm('Remover esta conta?')) {
      setRemoving(true)
      try { await api.del(`/accounts/${account.id}`); refresh?.() }
      catch (e) { alert('Falha ao remover conta: ' + e.message) }
      finally { setRemoving(false) }
    }
  }

  const changeLang = async (code) => {
    setLang(code); setSavingLang(true)
    try { await api.patch(`/accounts/${account.id}`, { content_language: code }); refresh?.() }
    catch (e) { alert('Falha ao salvar idioma: ' + e.message); setLang(account.content_language || 'pt-BR') }
    finally { setSavingLang(false) }
  }

  const changeVoice = async (vid) => {
    setVoice(vid); setSavingVoice(true)
    try { await api.patch(`/accounts/${account.id}`, { preferred_voice: vid }); refresh?.() }
    catch (e) { alert('Falha ao salvar voz: ' + e.message); setVoice(isClone ? '' : (account.preferred_voice || '')) }
    finally { setSavingVoice(false) }
  }

  const changeMusic = async (style) => {
    setMusic(style); setSavingMusic(true)
    try { await api.patch(`/accounts/${account.id}`, { music_style: style }); refresh?.() }
    catch (e) { alert('Falha ao salvar musica: ' + e.message); setMusic(account.music_style || 'balanced') }
    finally { setSavingMusic(false) }
  }

  const changeRide = async (on) => {
    setRide(on); setSavingRide(true)
    try { await api.patch(`/accounts/${account.id}`, { ride_trends: on }); refresh?.() }
    catch (e) { alert('Falha ao salvar: ' + e.message); setRide(account.ride_trends || false) }
    finally { setSavingRide(false) }
  }

  return (
    <div className="card card-hover p-3 sm:p-3.5" style={{ borderColor: blocked ? 'rgba(217,154,61,0.46)' : 'var(--border-glass)' }}>
      <div className="flex items-start gap-3">
        <div
          className="w-10 h-10 rounded-btn flex items-center justify-center font-black data shrink-0"
          style={{
            color: m.color,
            background: 'var(--accent-dim)',
            border: '1px solid var(--border-glass)',
          }}
        >
          {m.icon}
        </div>

        <div className="min-w-0 flex-1">
          <div className="flex items-start justify-between gap-2">
            <div className="min-w-0">
              <h3 className="heading text-base leading-tight truncate" title={account.display_name}>{account.display_name}</h3>
              <p className="text-xs mt-0.5 truncate" style={{ color: 'var(--text-muted)' }}>{account.niche || m.label}</p>
            </div>
            <span className="badge shrink-0" style={{ color: statusColor, background: 'var(--bg-elevated)' }}>
              <span className="w-1.5 h-1.5 rounded-full" style={{ background: connected ? 'var(--success)' : statusColor }} />
              {STATUS_LABEL[account.status] || account.status}
            </span>
          </div>

          <div className="mt-3 rounded-btn px-3 py-2" style={{ background: 'var(--bg-elevated)', border: '1px solid var(--border-glass)' }}>
            <div className="grid grid-cols-[minmax(0,1fr)_auto_auto] items-center gap-3">
              <div className="min-w-0">
                <p className="text-[10px] font-semibold uppercase" style={{ color: 'var(--text-dim)', letterSpacing: '.06em' }}>Uso hoje</p>
                <p className="text-[11px] truncate" style={{ color: blocked ? 'var(--warning)' : 'var(--text-muted)' }}>
                  {blocked ? 'Aguardando reset da quota' : `${account.quota_used_today}/${account.quota_limit} unidades`}
                </p>
              </div>
              <div className="text-right">
                <p className="data text-lg font-bold leading-none" style={{ color: blocked ? 'var(--warning)' : 'var(--text-primary)' }}>
                  {uploadsLeft == null ? '-' : uploadsLeft}
                </p>
                <p className="text-[10px]" style={{ color: 'var(--text-muted)' }}>uploads</p>
              </div>
              <div className="text-right">
                <p className="data text-lg font-bold leading-none">{quotaPct}<span className="text-xs">%</span></p>
                <p className="text-[10px]" style={{ color: 'var(--text-muted)' }}>quota</p>
              </div>
            </div>
            <div className="mt-2 h-1.5 rounded-full overflow-hidden" style={{ background: 'var(--bg-panel)' }}>
              <div
                className="h-full rounded-full transition-all"
                style={{ width: `${quotaPct}%`, background: blocked ? 'var(--warning)' : quotaPct > 85 ? 'var(--warning)' : m.color }}
              />
            </div>
          </div>

          <div className="mt-3 flex flex-wrap gap-2">
            {connected
              ? <button className="btn-ghost btn-sm" style={{ color: 'var(--error)' }} onClick={disconnect}>Desconectar</button>
              : <button className="btn-primary btn-sm" onClick={connect}>Conectar {m.label}</button>
            }
            <button className="btn-ghost btn-sm" disabled={toggling} onClick={toggle}>
              {toggling ? 'Aguarde...' : (account.status === 'active' ? 'Pausar' : 'Retomar')}
            </button>
            <button className="btn-ghost btn-sm" onClick={() => setExpanded((v) => !v)}>
              {expanded ? 'Fechar ajustes' : 'Ajustes'}
            </button>
          </div>
        </div>
      </div>

      {expanded && (
        <div className="slide-down mt-4 pt-4 space-y-4" style={{ borderTop: '1px solid var(--border-glass)' }}>
          {connected && account.platform === 'youtube' && (
            <button
              className="btn-ghost w-full justify-center"
              style={{ background: 'var(--accent-dim)', color: 'var(--accent)', borderColor: 'var(--border-glow)' }}
              onClick={() => setOptimizing(true)}
            >
              Otimizar canal
              {account.channel_optimization?.activated && (
                <span className="badge" style={{ background: 'var(--bg-elevated)', color: 'var(--success)' }}>Ativo</span>
              )}
            </button>
          )}

          <div className="grid sm:grid-cols-2 gap-3">
            <label className="space-y-1">
              <span className="text-xs font-semibold" style={{ color: 'var(--text-muted)' }}>Idioma {savingLang && '(salvando)'}</span>
              <select className="input" value={lang} disabled={savingLang} onChange={(e) => changeLang(e.target.value)}>
                {LANGUAGES.map((l) => <option key={l.code} value={l.code}>{l.label}</option>)}
              </select>
            </label>

            <label className="space-y-1">
              <span className="text-xs font-semibold" style={{ color: 'var(--text-muted)' }}>Voz do canal {savingVoice && '(salvando)'}</span>
              <select className="input" value={isClone ? '' : voice} disabled={savingVoice} onChange={(e) => changeVoice(e.target.value)}>
                {isClone && <option value="">Voz clonada (LMNT)</option>}
                {voicesForLang(lang).map((v) => <option key={v.id} value={v.id}>{v.label}</option>)}
              </select>
            </label>

            <label className="space-y-1">
              <span className="text-xs font-semibold" style={{ color: 'var(--text-muted)' }}>Estilo de musica {savingMusic && '(salvando)'}</span>
              <select className="input" value={music} disabled={savingMusic} onChange={(e) => changeMusic(e.target.value)}>
                <option value="energetic">Highlight - batida forte</option>
                <option value="balanced">Equilibrado</option>
                <option value="calm">Calmo - fundo suave</option>
              </select>
            </label>

            <div className="space-y-1">
              <span className="text-xs font-semibold" style={{ color: 'var(--text-muted)' }}>Momento em alta {savingRide && '(salvando)'}</span>
              <button className="input flex items-center justify-between" disabled={savingRide} onClick={() => changeRide(!ride)}>
                <span>{ride ? 'Ativado no nicho' : 'Desativado'}</span>
                <span className="badge" style={ride ? { color: 'var(--warning)' } : { color: 'var(--text-muted)' }}>{ride ? 'ON' : 'OFF'}</span>
              </button>
            </div>
          </div>

          <div className="flex flex-wrap gap-2">
            <button className="btn-ghost btn-sm" onClick={() => setRecording(true)}>
              {isClone ? 'Regravar voz clonada' : 'Clonar minha voz'}
            </button>
            <button className="btn-ghost btn-sm" style={{ color: 'var(--error)' }} disabled={removing} onClick={remove}>
              {removing ? 'Removendo...' : 'Remover conta'}
            </button>
          </div>
        </div>
      )}

      {recording && (
        <VoiceRecorder
          account={account}
          onClose={() => setRecording(false)}
          onCloned={() => { setRecording(false); refresh?.() }}
        />
      )}

      {optimizing && (
        <ChannelOptimizer
          account={account}
          onClose={() => setOptimizing(false)}
          onApplied={() => refresh?.()}
        />
      )}
    </div>
  )
}
