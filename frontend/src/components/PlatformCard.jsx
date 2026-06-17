import { useState } from 'react'
import { api } from '../api'
import { PLATFORM_META, LANGUAGES, voicesForLang } from '../lib'
import VoiceRecorder from './VoiceRecorder.jsx'

export default function PlatformCard({ account, onChange, onChanged, onDone }) {
  const [recording, setRecording] = useState(false)
  const [expanded, setExpanded] = useState(false)
  const [lang, setLang] = useState(account.content_language || 'pt-BR')
  const [savingLang, setSavingLang] = useState(false)
  const isClone = account.preferred_voice?.startsWith('v_')
  const [voice, setVoice] = useState(isClone ? '' : (account.preferred_voice || ''))
  const [savingVoice, setSavingVoice] = useState(false)
  const m = PLATFORM_META[account.platform] || { label: account.platform, color: 'var(--accent)', icon: '●' }
  const quotaPct = account.quota_limit ? Math.round((account.quota_used_today / account.quota_limit) * 100) : 0
  const refresh = onChange || onChanged || onDone
  const connected = account.has_credentials === true

  const connect = async () => {
    try {
      const { auth_url } = await api.get(`/auth/${account.platform}/start?account_id=${account.id}`)
      window.open(auth_url, '_blank', 'width=600,height=720')
    } catch (e) { alert('OAuth indisponível: ' + e.message) }
  }
  const disconnect = async () => {
    try { await api.post(`/accounts/${account.id}/disconnect`); refresh?.() }
    catch (e) { alert('Falha ao desconectar: ' + e.message) }
  }
  const toggle = async () => {
    await api.post(`/accounts/${account.id}/${account.status === 'active' ? 'pause' : 'resume'}`)
    refresh?.()
  }
  const remove = async () => {
    if (confirm('Remover esta conta?')) { await api.del(`/accounts/${account.id}`); refresh?.() }
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

  const statusColor = { active: 'var(--success)', paused: 'var(--text-muted)', quota_exceeded: 'var(--warning)', auth_error: 'var(--error)' }[account.status] || 'var(--text-muted)'

  return (
    <div className="card flex flex-col transition-all duration-300" style={{ borderColor: m.color + '35' }}>

      {/* ── Logo + nome + status dot ── */}
      <div className="flex flex-col items-center pt-5 pb-3 px-3 gap-2">
        <div className="relative">
          <div
            className="w-14 h-14 rounded-2xl flex items-center justify-center text-3xl select-none"
            style={{
              background: m.color + '18',
              border: '2px solid ' + m.color + '50',
              boxShadow: '0 0 20px ' + m.color + '25',
            }}
          >
            {m.icon}
          </div>
          {/* Status dot */}
          <span
            className="absolute -top-1 -right-1 w-3.5 h-3.5 rounded-full border-2"
            style={{
              background: connected ? 'var(--success)' : statusColor,
              borderColor: 'var(--bg-base)',
              boxShadow: connected ? '0 0 6px rgba(0,245,160,0.7)' : 'none',
            }}
          />
        </div>
        <div className="text-center w-full px-1">
          <p className="font-semibold text-sm leading-tight truncate" title={account.display_name}>{account.display_name}</p>
          <p className="text-[11px] text-text-muted truncate">{account.niche || m.label}</p>
        </div>
      </div>

      {/* ── Ação principal + controles ── */}
      <div className="flex items-center gap-1 px-3 pb-3">
        {connected
          ? <button className="btn-ghost flex-1 text-[11px] py-1.5" style={{ color: 'var(--error)' }} onClick={disconnect}>Desconectar</button>
          : <button className="btn-primary flex-1 text-[11px] py-1.5" onClick={connect}>🔗 Conectar</button>
        }
        <button
          className="btn-ghost text-xs px-2 py-1.5"
          onClick={toggle}
          title={account.status === 'active' ? 'Pausar' : 'Retomar'}
        >
          {account.status === 'active' ? '⏸' : '▶'}
        </button>
        <button
          className="btn-ghost text-xs px-2 py-1.5"
          onClick={() => setExpanded((v) => !v)}
          title="Configurações"
          style={{ color: expanded ? 'var(--accent)' : undefined, transition: 'transform .2s', transform: expanded ? 'rotate(45deg)' : 'none' }}
        >
          ⚙
        </button>
      </div>

      {/* ── Painel expansível ── */}
      {expanded && (
        <div
          className="slide-down space-y-3 px-3 pb-4 border-t pt-3"
          style={{ borderColor: 'rgba(255,255,255,0.06)' }}
        >
          {/* Quota */}
          <div>
            <div className="flex justify-between text-[11px] text-text-muted mb-1">
              <span>Quota diária</span>
              <span style={{ color: quotaPct > 80 ? 'var(--warning)' : undefined }}>
                {account.quota_used_today}/{account.quota_limit}
              </span>
            </div>
            <div className="h-1.5 rounded-full overflow-hidden" style={{ background: 'var(--bg-elevated)' }}>
              <div
                className="h-full rounded-full transition-all"
                style={{ width: `${quotaPct}%`, background: quotaPct > 90 ? 'var(--error)' : m.color }}
              />
            </div>
          </div>

          {/* Idioma */}
          <div>
            <label className="flex items-center justify-between text-[11px] text-text-muted mb-1">
              <span>🌐 Idioma</span>
              {savingLang && <span className="opacity-60">salvando…</span>}
            </label>
            <select className="input w-full text-xs" value={lang} disabled={savingLang} onChange={(e) => changeLang(e.target.value)}>
              {LANGUAGES.map((l) => <option key={l.code} value={l.code}>{l.label}</option>)}
            </select>
          </div>

          {/* Voz (grátis, edge-tts) */}
          <div>
            <label className="flex items-center justify-between text-[11px] text-text-muted mb-1">
              <span>🔊 Voz do canal</span>
              {savingVoice && <span className="opacity-60">salvando…</span>}
            </label>
            <select
              className="input w-full text-xs"
              value={isClone ? '' : voice}
              disabled={savingVoice}
              onChange={(e) => changeVoice(e.target.value)}
            >
              {isClone && <option value="">● voz clonada (LMNT)</option>}
              {voicesForLang(lang).map((v) => <option key={v.id} value={v.id}>{v.label}</option>)}
            </select>
            <p className="text-[10px] text-text-muted mt-1">Vozes prontas grátis — escolha uma diferente por canal.</p>
          </div>

          {/* Clonar a própria voz (LMNT) */}
          <button
            className={`${isClone ? 'btn-ghost' : 'btn-ghost'} w-full text-xs`}
            onClick={() => setRecording(true)}
          >
            <span>🎤</span>
            <span className="flex-1 text-left">{isClone ? 'Regravar voz clonada' : 'Clonar minha voz (LMNT)'}</span>
            {isClone && (
              <span className="badge text-[10px]" style={{ background: 'rgba(0,245,160,0.15)', color: 'var(--success)' }}>● clonada</span>
            )}
          </button>

          {/* Remover */}
          <button className="btn-ghost w-full text-xs" style={{ color: 'var(--error)' }} onClick={remove}>
            🗑 Remover conta
          </button>
        </div>
      )}

      {recording && (
        <VoiceRecorder
          account={account}
          onClose={() => setRecording(false)}
          onCloned={() => { setRecording(false); refresh?.() }}
        />
      )}
    </div>
  )
}
