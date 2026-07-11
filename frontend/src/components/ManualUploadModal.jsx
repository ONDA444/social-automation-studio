import { useState } from 'react'
import { createPortal } from 'react-dom'
import { api } from '../api'

// "Enviar video do PC": upload a local file, analyze it with the same engine
// used for Drive ready-videos, and land it in Aprovações. Standalone one-off
// job — never joins the Drive rotation/inventory.
export default function ManualUploadModal({ open, onClose, accountId }) {
  const [file, setFile]   = useState(null)
  const [hint, setHint]   = useState('')
  const [when, setWhen]   = useState('now') // 'now' | 'later'
  const [at, setAt]       = useState('')
  const [busy, setBusy]   = useState(false)
  const [msg, setMsg]     = useState('')

  if (!open) return null

  const reset = () => { setFile(null); setHint(''); setWhen('now'); setAt(''); setMsg('') }
  const close = () => { if (!busy) { reset(); onClose() } }

  const submit = async () => {
    if (!accountId) return setMsg('Selecione uma conta primeiro.')
    if (!file) return setMsg('Escolha um arquivo de vídeo.')
    setBusy(true)
    setMsg('Enviando...')
    try {
      const fields = { hint }
      if (when === 'later' && at) fields.scheduled_at = at
      await api.upload(`/schedule/${accountId}/upload-video`, file, fields)
      reset()
      onClose()
      alert('Vídeo recebido — analisando... vai aparecer em Aprovações em instantes.')
    } catch (e) {
      setMsg(e.message || 'Falha ao enviar o vídeo.')
    } finally {
      setBusy(false)
    }
  }

  // Rendered via portal straight onto document.body: `fixed` positioning only
  // fills the viewport when NO ancestor has a `transform` (creates a new
  // containing block otherwise). The page wrapper's `.fade-in` animation
  // leaves a residual `transform: matrix(...)` after finishing
  // (animation-fill-mode: both), which silently broke `fixed inset-0` for any
  // modal nested inside it — the backdrop rendered pinned to the SCROLLABLE
  // CONTENT AREA's box instead of the viewport, pushing the dialog itself off
  // screen. A portal escapes that ancestor entirely.
  return createPortal(
    <div className="fixed inset-0 bg-black/60 backdrop-blur-sm grid place-items-center z-50 p-3 sm:p-4 fade-in overflow-y-auto" onClick={close}>
      <div className="card p-4 sm:p-6 w-full max-w-lg max-h-[90vh] overflow-y-auto shadow-[0_20px_60px_rgba(0,0,0,0.5)] animate-[fadeIn_.2s_ease-out] origin-center" onClick={(e) => e.stopPropagation()}>
        <h3 className="heading text-lg font-semibold mb-1">Enviar vídeo do PC</h3>
        <p className="text-xs text-text-muted mb-4">
          O sistema analisa o vídeo, gera título/descrição e o coloca em Aprovações pra você revisar antes de publicar.
        </p>
        <div className="space-y-3">
          <div>
            <label className="text-xs text-text-muted">Arquivo de vídeo</label>
            <input
              type="file"
              accept="video/mp4,video/quicktime,video/webm,video/x-matroska,.mp4,.mov,.webm,.mkv"
              className="input mt-1"
              onChange={(e) => setFile(e.target.files?.[0] || null)}
            />
            {file && <p className="text-[11px] text-text-muted mt-1">{file.name} ({(file.size / (1024 * 1024)).toFixed(1)} MB)</p>}
          </div>
          <div>
            <label className="text-xs text-text-muted">Dica de contexto (opcional)</label>
            <input
              type="text"
              className="input mt-1"
              placeholder="Ex: sobre motociclista fugindo da polícia"
              value={hint}
              maxLength={200}
              onChange={(e) => setHint(e.target.value)}
            />
          </div>
          <div>
            <label className="text-xs text-text-muted">Quando publicar</label>
            <div className="flex gap-2 mt-1">
              {[
                { v: 'now', label: 'Agora' },
                { v: 'later', label: 'Agendar para' },
              ].map((o) => (
                <button key={o.v} type="button" onClick={() => setWhen(o.v)}
                  className={`flex-1 rounded-card border p-2 text-center text-xs transition-colors ${when === o.v ? 'border-accent bg-accent/15 text-text-primary' : 'border-border text-text-muted hover:bg-elevated'}`}>
                  {o.label}
                </button>
              ))}
            </div>
            {when === 'later' && (
              <input
                type="datetime-local"
                className="input mt-2"
                value={at}
                onChange={(e) => setAt(e.target.value)}
              />
            )}
          </div>
          {msg && <p className="text-[11px] text-warning">{msg}</p>}
        </div>
        <div className="flex flex-col-reverse sm:flex-row gap-2 sm:justify-end mt-5">
          <button className="btn-ghost w-full sm:w-auto" onClick={close} disabled={busy}>Cancelar</button>
          <button className="btn-primary w-full sm:w-auto" disabled={busy} onClick={submit}>
            {busy ? 'Enviando...' : 'Enviar'}
          </button>
        </div>
      </div>
    </div>,
    document.body
  )
}
