import { useState } from 'react'
import { api } from '../api'
import { Modal } from './ui.jsx'

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

  return (
    <Modal open={open} onClose={close} labelledBy="manual-upload-modal-title">
        <h3 id="manual-upload-modal-title" className="heading text-lg font-semibold mb-1">Enviar vídeo do PC</h3>
        <p className="text-xs text-text-muted mb-4">
          O sistema analisa o vídeo, gera título/descrição e o coloca em Aprovações pra você revisar antes de publicar.
        </p>
        <div className="space-y-3">
          <div>
            <label htmlFor="manual-upload-file" className="text-xs text-text-muted">Arquivo de vídeo</label>
            <input
              id="manual-upload-file"
              type="file"
              accept="video/mp4,video/quicktime,video/webm,video/x-matroska,.mp4,.mov,.webm,.mkv"
              className="input mt-1"
              onChange={(e) => setFile(e.target.files?.[0] || null)}
            />
            {file && <p className="text-[11px] text-text-muted mt-1">{file.name} ({(file.size / (1024 * 1024)).toFixed(1)} MB)</p>}
          </div>
          <div>
            <label htmlFor="manual-upload-hint" className="text-xs text-text-muted">Dica de contexto (opcional)</label>
            <input
              id="manual-upload-hint"
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
    </Modal>
  )
}
