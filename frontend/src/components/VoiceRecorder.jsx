import { useRef, useState } from 'react'
import { api } from '../api'

export default function VoiceRecorder({ account, onClose, onCloned }) {
  const [mode, setMode] = useState('record')

  // ── Gravação ──
  const [recording, setRecording] = useState(false)
  const [seconds, setSeconds] = useState(0)
  const [recorded, setRecorded] = useState(false)
  const mediaRef = useRef(null)
  const chunksRef = useRef([])
  const timerRef = useRef(null)

  // ── Upload ──
  const [uploadFile, setUploadFile] = useState(null)
  const fileInputRef = useRef(null)

  // ── Shared ──
  const [status, setStatus] = useState('')
  const [busy, setBusy] = useState(false)
  const MAX = 30

  const start = async () => {
    setStatus(''); setRecorded(false)
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
      const mr = new MediaRecorder(stream)
      chunksRef.current = []
      mr.ondataavailable = (e) => { if (e.data && e.data.size) chunksRef.current.push(e.data) }
      mr.onstop = () => { stream.getTracks().forEach((t) => t.stop()); setRecorded(chunksRef.current.length > 0) }
      mr.start()
      mediaRef.current = mr
      setRecording(true); setSeconds(0)
      timerRef.current = setInterval(() => setSeconds((s) => {
        if (s + 1 >= MAX) { stop() }
        return s + 1
      }), 1000)
    } catch (e) {
      setStatus('Não consegui acessar o microfone. Permita o acesso no navegador. (' + e.message + ')')
    }
  }

  const stop = () => {
    clearInterval(timerRef.current)
    setRecording(false)
    const mr = mediaRef.current
    if (mr && mr.state !== 'inactive') mr.stop()
  }

  const cloneFromMic = async () => {
    if (!chunksRef.current.length) { setStatus('Grave sua voz primeiro.'); return }
    setBusy(true); setStatus('Clonando sua voz no LMNT…')
    try {
      const blob = new Blob(chunksRef.current, { type: 'audio/webm' })
      const file = new File([blob], 'voice.webm', { type: 'audio/webm' })
      const r = await api.upload(`/accounts/${account.id}/clone-voice`, file)
      setStatus('✓ Voz clonada e definida como a voz deste canal!')
      onCloned?.(r)
    } catch (e) {
      setStatus('Erro ao clonar: ' + e.message)
    } finally { setBusy(false) }
  }

  const cloneFromFile = async () => {
    if (!uploadFile) { setStatus('Selecione um arquivo de áudio primeiro.'); return }
    setBusy(true); setStatus('Enviando arquivo e clonando voz…')
    try {
      const r = await api.upload(`/accounts/${account.id}/clone-voice`, uploadFile)
      setStatus('✓ Voz clonada e definida como a voz deste canal!')
      onCloned?.(r)
    } catch (e) {
      setStatus('Erro ao clonar: ' + e.message)
    } finally { setBusy(false) }
  }

  const pickFile = (e) => {
    const f = e.target.files?.[0]
    if (f) { setUploadFile(f); setStatus('') }
  }

  const fmtSize = (bytes) => bytes < 1024 * 1024
    ? `${(bytes / 1024).toFixed(0)} KB`
    : `${(bytes / (1024 * 1024)).toFixed(1)} MB`

  return (
    <div className="fixed inset-0 bg-black/60 backdrop-blur-sm grid place-items-center z-50 p-3 sm:p-4 fade-in overflow-y-auto" onClick={onClose}>
      <div className="card p-4 sm:p-6 w-full max-w-md max-h-[90vh] overflow-y-auto" onClick={(e) => e.stopPropagation()}>
        <h3 className="heading text-lg font-semibold mb-1">🎙️ Voz do canal</h3>
        <p className="text-xs text-text-muted mb-4">
          Clone sua voz e use nas narrações de <b>{account.display_name}</b>.
          Fale ou envie um áudio claro de <b>10 a 30 segundos</b>.
        </p>

        {/* Abas */}
        <div className="flex gap-1 mb-4 p-1 rounded-xl" style={{ background: 'rgba(255,255,255,0.04)', border: '1px solid rgba(255,255,255,0.06)' }}>
          {[{ key: 'record', label: '🎤 Gravar' }, { key: 'upload', label: '📂 Enviar arquivo' }].map(({ key, label }) => (
            <button
              key={key}
              className="flex-1 text-xs py-1.5 rounded-lg transition-all"
              style={{
                background: mode === key ? 'rgba(124,106,255,0.2)' : 'transparent',
                color: mode === key ? 'var(--accent)' : 'var(--text-muted)',
                border: mode === key ? '1px solid rgba(124,106,255,0.3)' : '1px solid transparent',
              }}
              onClick={() => { setMode(key); setStatus('') }}
            >
              {label}
            </button>
          ))}
        </div>

        {/* ── Aba Gravar ── */}
        {mode === 'record' && (
          <>
            <div className="grid place-items-center py-6">
              <div className={`w-24 h-24 rounded-full grid place-items-center text-3xl transition-all ${recording ? 'bg-error/20 animate-pulse' : 'bg-elevated'}`}>
                🎙️
              </div>
              <p className="text-2xl font-mono mt-3">{String(Math.floor(seconds / 60)).padStart(2, '0')}:{String(seconds % 60).padStart(2, '0')}</p>
              <p className="text-[11px] text-text-muted">{recording ? 'Gravando… (para sozinho em 30s)' : recorded ? 'Gravação pronta' : 'Pronto pra gravar'}</p>
            </div>

            {status && <p className="text-xs mb-3 text-center" style={{ color: status.startsWith('✓') ? 'var(--success)' : 'var(--error)' }}>{status}</p>}

            <div className="flex items-center gap-2">
              {!recording
                ? <button className="btn-primary flex-1" onClick={start} disabled={busy}>{recorded ? '↻ Regravar' : '● Gravar'}</button>
                : <button className="btn-ghost flex-1" style={{ color: 'var(--error)' }} onClick={stop}>■ Parar</button>}
              <button className="btn-primary flex-1" onClick={cloneFromMic} disabled={!recorded || busy || recording}>
                {busy ? 'Clonando…' : 'Usar esta voz'}
              </button>
            </div>
          </>
        )}

        {/* ── Aba Upload ── */}
        {mode === 'upload' && (
          <>
            <div
              className="rounded-xl border-2 border-dashed grid place-items-center py-8 mb-4 cursor-pointer transition-colors"
              style={{ borderColor: uploadFile ? 'rgba(0,245,160,0.5)' : 'rgba(255,255,255,0.12)', background: 'rgba(255,255,255,0.02)' }}
              onClick={() => fileInputRef.current?.click()}
            >
              <input ref={fileInputRef} type="file" accept="audio/*,.mp3,.wav,.m4a,.ogg,.flac,.webm" className="hidden" onChange={pickFile} />
              {uploadFile ? (
                <div className="text-center px-4">
                  <p className="text-2xl mb-2">🎵</p>
                  <p className="text-sm font-medium truncate max-w-[240px]" title={uploadFile.name}>{uploadFile.name}</p>
                  <p className="text-xs text-text-muted mt-1">{fmtSize(uploadFile.size)}</p>
                  <p className="text-[10px] mt-2" style={{ color: 'var(--success)' }}>Clique para trocar o arquivo</p>
                </div>
              ) : (
                <div className="text-center px-4">
                  <p className="text-3xl mb-2">📂</p>
                  <p className="text-sm font-medium">Clique para selecionar</p>
                  <p className="text-xs text-text-muted mt-1">MP3, WAV, M4A, OGG, FLAC — 10 a 30 segundos</p>
                </div>
              )}
            </div>

            {status && <p className="text-xs mb-3 text-center" style={{ color: status.startsWith('✓') ? 'var(--success)' : status.startsWith('Erro') ? 'var(--error)' : 'var(--text-muted)' }}>{status}</p>}

            <button className="btn-primary w-full" onClick={cloneFromFile} disabled={!uploadFile || busy}>
              {busy ? 'Clonando…' : 'Clonar voz deste arquivo'}
            </button>
          </>
        )}

        <button className="btn-ghost w-full text-xs mt-2" onClick={onClose}>Fechar</button>
      </div>
    </div>
  )
}
