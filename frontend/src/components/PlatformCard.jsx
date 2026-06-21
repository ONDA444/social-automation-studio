import { useState } from 'react'
import { api } from '../api'
import { PLATFORM_META, LANGUAGES, voicesForLang } from '../lib'
import VoiceRecorder from './VoiceRecorder.jsx'
import ChannelOptimizer from './ChannelOptimizer.jsx'

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
  const changeMusic = async (style) => {
    setMusic(style); setSavingMusic(true)
    try { await api.patch(`/accounts/${account.id}`, { music_style: style }); refresh?.() }
    catch (e) { alert('Falha ao salvar música: ' + e.message); setMusic(account.music_style || 'balanced') }
    finally { setSavingMusic(false) }
  }
  const changeRide = async (on) => {
    setRide(on); setSavingRide(true)
    try { await api.patch(`/accounts/${account.id}`, { ride_trends: on }); refresh?.() }
    catch (e) { alert('Falha ao salvar: ' + e.message); setRide(account.ride_trends || false) }
    finally { setSavingRide(false) }
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
          : <button
              className="flex-1 inline-flex items-center justify-center gap-1.5 text-xs font-semibold text-white rounded-btn py-2 transition-all hover:brightness-110"
              style={{ background: m.color, boxShadow: `0 1px 10px ${m.color}55` }}
              onClick={connect}>
              <span>{m.icon}</span>
              <span>{account.platform === 'youtube' ? 'Entrar com Google' : `Conectar ${m.label}`}</span>
            </button>
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
          {/* ✨ Otimizar canal (YouTube conectado) */}
          {connected && account.platform === 'youtube' && (
            <button
              className="w-full text-xs font-semibold rounded-btn py-2 flex items-center justify-center gap-1.5 transition-all hover:brightness-110"
              style={{ background: 'var(--accent-dim)', color: 'var(--accent)', border: '1px solid var(--accent)' }}
              onClick={() => setOptimizing(true)}
            >
              ✨ Otimizar canal
              {account.channel_optimization?.activated && (
                <span className="badge text-[9px]" style={{ background: 'rgba(0,245,160,0.15)', color: 'var(--success)' }}>● ativo</span>
              )}
            </button>
          )}

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

          {/* Estilo de música (vibe por canal) */}
          <div>
            <label className="flex items-center justify-between text-[11px] text-text-muted mb-1">
              <span>🎵 Estilo de música</span>
              {savingMusic && <span className="opacity-60">salvando…</span>}
            </label>
            <select className="input w-full text-xs" value={music} disabled={savingMusic} onChange={(e) => changeMusic(e.target.value)}>
              <option value="energetic">🔥 Highlight — batida forte</option>
              <option value="balanced">🎚️ Equilibrado (padrão)</option>
              <option value="calm">🌙 Calmo — fundo suave</option>
            </select>
            <p className="text-[10px] text-text-muted mt-1">Highlight = trilha energética estilo viral. Vale para os próximos vídeos.</p>
          </div>

          {/* 🔥 Momento em alta (opt-in por canal) */}
          <div>
            <label className="flex items-center justify-between text-[11px] text-text-muted mb-1">
              <span>🔥 Momento em alta</span>
              {savingRide && <span className="opacity-60">salvando…</span>}
            </label>
            <button
              className="input w-full text-xs flex items-center justify-between"
              disabled={savingRide}
              onClick={() => changeRide(!ride)}
            >
              <span>{ride ? 'Ativado — pega o que está bombando no nicho' : 'Desativado'}</span>
              <span className="badge text-[10px]"
                style={ride ? { background: 'rgba(255,182,39,0.16)', color: 'var(--warning)' }
                            : { background: 'rgba(255,255,255,0.06)', color: 'var(--text-muted)' }}>
                {ride ? '● ON' : 'OFF'}
              </span>
            </button>
            <p className="text-[10px] text-text-muted mt-1">Gera 1–2 vídeos do assunto em alta e <strong>publica direto</strong> no canal (sem aprovação).</p>
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
