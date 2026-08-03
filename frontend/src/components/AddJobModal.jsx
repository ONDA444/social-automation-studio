import { useEffect, useState } from 'react'
import { api } from '../api'
import { useAccounts } from '../AccountsContext.jsx'
import { Modal } from './ui.jsx'
import { FALLBACK_CONTENT_TYPES } from '../lib'

const PLATFORMS = ['youtube', 'tiktok', 'instagram']

export default function AddJobModal({ open, onClose, onCreated }) {
  const [form, setForm] = useState({ title: '', content_type: 'auto', format: 'long', target_platforms: ['youtube'], account_id: '' })
  const { accounts } = useAccounts()
  const [contentTypes, setContentTypes] = useState(FALLBACK_CONTENT_TYPES)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    if (!open) return
    // Pre-bind to the first channel so the video uses THAT channel's voice/language.
    // Leaving it unset made narration fall back to the generic default voice.
    setForm((f) => (f.account_id || !accounts.length) ? f : { ...f, account_id: String(accounts[0].id) })
  }, [open, accounts])

  useEffect(() => {
    if (!open) return
    api.get('/jobs/content-types')
      .then((d) => { const list = Array.isArray(d) ? d : d?.content_types; if (Array.isArray(list) && list.length) setContentTypes(list) })
      .catch(() => setContentTypes(FALLBACK_CONTENT_TYPES))
  }, [open])

  const toggle = (p) => setForm((f) => ({ ...f, target_platforms: f.target_platforms.includes(p) ? f.target_platforms.filter((x) => x !== p) : [...f.target_platforms, p] }))

  // Split por quebra-de-linha E por vírgula, trim, remove vazios.
  const parseThemes = (raw) => (raw || '').split(/[\n,]+/).map((t) => t.trim()).filter(Boolean)
  const themes = parseThemes(form.title)

  const submit = async () => {
    if (themes.length === 0) return alert('Informe ao menos um título/tema')
    setBusy(true)
    try {
      const account_id = form.account_id ? Number(form.account_id) : null
      if (themes.length === 1) {
        await api.post('/jobs', { title: themes[0], content_type: form.content_type, format: form.format, target_platforms: form.target_platforms, account_id })
      } else {
        await api.post('/jobs/batch', { themes, content_type: form.content_type, format: form.format, target_platforms: form.target_platforms, account_id })
      }
      onCreated?.()
      onClose()
      setForm({ title: '', content_type: 'auto', format: 'long', target_platforms: ['youtube'], account_id: '' })
    } catch (e) { alert(e.message) } finally { setBusy(false) }
  }

  return (
    <Modal open={open} onClose={onClose} labelledBy="add-job-modal-title">
        <h3 id="add-job-modal-title" className="heading text-lg font-semibold mb-4">Novo vídeo</h3>
        <div className="space-y-3">
          <div>
            <label htmlFor="addjob-title" className="text-xs text-text-muted">Título / Temas</label>
            <textarea id="addjob-title" className="input mt-1 min-h-[88px] resize-y" rows={4} value={form.title} onChange={(e) => setForm({ ...form, title: e.target.value })} placeholder="Um tema por linha (ou separados por vírgula). Ex: Copa 1958, Copa 1970, ..." autoFocus />
            <p className="text-[11px] text-text-muted mt-1">{themes.length} {themes.length === 1 ? 'tema detectado' : 'temas detectados'}</p>
          </div>
          <div>
            <label htmlFor="addjob-content-type" className="text-xs text-text-muted">Tipo de conteúdo</label>
            <select id="addjob-content-type" className="input mt-1" value={form.content_type} onChange={(e) => setForm({ ...form, content_type: e.target.value })}>
              {contentTypes.map((c) => <option key={c.value} value={c.value}>{c.label}</option>)}
            </select>
            {form.content_type === 'auto' && (
              <p className="text-[11px] text-accent mt-1">✨ A IA detecta o melhor tipo com base no tema que você escreveu.</p>
            )}
          </div>
          <div>
            <label className="text-xs text-text-muted">Formato</label>
            <div className="flex gap-2 mt-1">
              {[
                { v: 'long', label: '🖥️ Vídeo longo', hint: '16:9 • YouTube' },
                { v: 'short', label: '📱 Shorts', hint: '9:16 • TikTok/Reels/Shorts' },
              ].map((f) => (
                <button key={f.v} type="button" onClick={() => setForm({ ...form, format: f.v })}
                  className={`flex-1 rounded-card border p-2 text-left text-xs transition-colors ${form.format === f.v ? 'border-accent bg-accent/15 text-text-primary' : 'border-border text-text-muted hover:bg-elevated'}`}>
                  <div className="font-semibold">{f.label}</div>
                  <div className="text-[10px] opacity-70">{f.hint}</div>
                </button>
              ))}
            </div>
          </div>
          <div>
            <label className="text-xs text-text-muted">Plataformas</label>
            <div className="flex gap-2 mt-1">
              {PLATFORMS.map((p) => (
                <button key={p} onClick={() => toggle(p)} className={`badge cursor-pointer transition-colors ${form.target_platforms.includes(p) ? 'bg-accent text-white' : 'bg-elevated text-text-muted hover:text-text-primary hover:bg-border'}`}>{p}</button>
              ))}
            </div>
          </div>
          <div>
            <label htmlFor="addjob-account" className="text-xs text-text-muted">Canal (define a voz e o idioma)</label>
            <select id="addjob-account" className="input mt-1" value={form.account_id} onChange={(e) => setForm({ ...form, account_id: e.target.value })}>
              <option value="">— nenhum (voz padrão) —</option>
              {accounts.map((a) => <option key={a.id} value={a.id}>{a.display_name} ({a.platform})</option>)}
            </select>
            <p className="text-[11px] text-text-muted mt-1">A narração usa a voz configurada nesse canal. Sem canal, usa a voz padrão genérica.</p>
          </div>
        </div>
        <div className="flex flex-col-reverse sm:flex-row gap-2 sm:justify-end mt-5">
          <button className="btn-ghost w-full sm:w-auto" onClick={onClose}>Cancelar</button>
          <button className="btn-primary w-full sm:w-auto" disabled={busy} onClick={submit}>{busy ? 'Criando...' : (themes.length > 1 ? `Criar ${themes.length} vídeos` : 'Criar e gerar')}</button>
        </div>
    </Modal>
  )
}
