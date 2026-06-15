import { useRef, useState } from 'react'
import { api } from '../api'

// Record a voice sample with the mic and clone it (LMNT) as the channel's voice.
export default function VoiceRecorder({ account, onClose, onCloned }) {
  const [recording, setRecording] = useState(false)
  const [seconds, setSeconds] = useState(0)
  const [recorded, setRecorded] = useState(false)
  const [status, setStatus] = useState('')
  const [busy, setBusy] = useState(false)
  const mediaRef = useRef(null)
  const chunksRef = useRef([])
  const timerRef = useRef(null)

  const MAX = 30 // auto-stop seconds

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

  const clone = async () => {
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

  return (
    <div className="fixed inset-0 bg-black/60 backdrop-blur-sm grid place-items-center z-50 p-4 fade-in" onClick={onClose}>
      <div className="card p-6 w-full max-w-md" onClick={(e) => e.stopPropagation()}>
        <h3 className="heading text-lg font-semibold mb-1">🎤 Gravar minha voz</h3>
        <p className="text-xs text-text-muted mb-4">
          Fale de forma clara por <b>10 a 30 segundos</b> (pode ler um texto qualquer).
          O sistema cria um clone da sua voz e passa a narrar <b>{account.display_name}</b> com ela.
        </p>

        <div className="grid place-items-center py-6">
          <div className={`w-24 h-24 rounded-full grid place-items-center text-3xl transition-all ${recording ? 'bg-error/20 animate-pulse' : 'bg-elevated'}`}>
            🎙️
          </div>
          <p className="text-2xl font-mono mt-3">{String(Math.floor(seconds / 60)).padStart(2, '0')}:{String(seconds % 60).padStart(2, '0')}</p>
          <p className="text-[11px] text-text-muted">{recording ? 'Gravando… (para sozinho em 30s)' : recorded ? 'Gravação pronta' : 'Pronto pra gravar'}</p>
        </div>

        {status && <p className="text-xs mb-3 text-center" style={{ color: status.startsWith('✓') ? 'var(--success)' : status.startsWith('Erro') || status.includes('não') ? 'var(--error)' : 'var(--text-muted)' }}>{status}</p>}

        <div className="flex items-center gap-2">
          {!recording
            ? <button className="btn-primary flex-1" onClick={start} disabled={busy}>{recorded ? '↻ Regravar' : '● Gravar'}</button>
            : <button className="btn-ghost flex-1" style={{ color: 'var(--error)' }} onClick={stop}>■ Parar</button>}
          <button className="btn-primary flex-1" onClick={clone} disabled={!recorded || busy || recording}>{busy ? 'Clonando…' : 'Usar esta voz'}</button>
        </div>
        <button className="btn-ghost w-full text-xs mt-2" onClick={onClose}>Fechar</button>
      </div>
    </div>
  )
}
