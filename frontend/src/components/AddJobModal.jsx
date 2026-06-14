import { useEffect, useState } from 'react'
import { api } from '../api'

const CONTENT_TYPES = [
  { v: 'film_recap_ai_images', l: 'Recap (imagens IA)' },
  { v: 'sports_highlights', l: 'Esportes (highlights)' },
  { v: 'quote_viral', l: 'Frase viral' },
]
const PLATFORMS = ['youtube', 'tiktok', 'instagram']

export default function AddJobModal({ open, onClose, onCreated }) {
  const [form, setForm] = useState({ title: '', topic: '', content_type: 'film_recap_ai_images', target_platforms: ['youtube'], account_id: '' })
  const [accounts, setAccounts] = useState([])
  const [busy, setBusy] = useState(false)

  useEffect(() => { if (open) api.get('/accounts').then((d) => setAccounts(d.accounts)).catch(() => {}) }, [open])
  if (!open) return null

  const toggle = (p) => setForm((f) => ({ ...f, target_platforms: f.target_platforms.includes(p) ? f.target_platforms.filter((x) => x !== p) : [...f.target_platforms, p] }))

  const submit = async () => {
    if (!form.title.trim()) return alert('Informe um título/tema')
    setBusy(true)
    try {
      const payload = { ...form, account_id: form.account_id ? Number(form.account_id) : null }
      await api.post('/jobs', payload)
      onCreated?.()
      onClose()
      setForm({ title: '', topic: '', content_type: 'film_recap_ai_images', target_platforms: ['youtube'], account_id: '' })
    } catch (e) { alert(e.message) } finally { setBusy(false) }
  }

  return (
    <div className="fixed inset-0 bg-black/60 grid place-items-center z-50 p-4" onClick={onClose}>
      <div className="card p-6 w-full max-w-lg" onClick={(e) => e.stopPropagation()}>
        <h3 className="heading text-lg font-semibold mb-4">Novo vídeo</h3>
        <div className="space-y-3">
          <div>
            <label className="text-xs text-text-muted">Título / Tema</label>
            <input className="input mt-1" value={form.title} onChange={(e) => setForm({ ...form, title: e.target.value })} placeholder="Ex: O mistério do farol abandonado" autoFocus />
          </div>
          <div>
            <label className="text-xs text-text-muted">Tipo de conteúdo</label>
            <select className="input mt-1" value={form.content_type} onChange={(e) => setForm({ ...form, content_type: e.target.value })}>
              {CONTENT_TYPES.map((c) => <option key={c.v} value={c.v}>{c.l}</option>)}
            </select>
          </div>
          <div>
            <label className="text-xs text-text-muted">Plataformas</label>
            <div className="flex gap-2 mt-1">
              {PLATFORMS.map((p) => (
                <button key={p} onClick={() => toggle(p)} className={`badge cursor-pointer ${form.target_platforms.includes(p) ? 'bg-accent text-white' : 'bg-elevated text-text-muted'}`}>{p}</button>
              ))}
            </div>
          </div>
          <div>
            <label className="text-xs text-text-muted">Conta (workspace) — opcional</label>
            <select className="input mt-1" value={form.account_id} onChange={(e) => setForm({ ...form, account_id: e.target.value })}>
              <option value="">— nenhuma —</option>
              {accounts.map((a) => <option key={a.id} value={a.id}>{a.display_name} ({a.platform})</option>)}
            </select>
          </div>
        </div>
        <div className="flex gap-2 justify-end mt-5">
          <button className="btn-ghost" onClick={onClose}>Cancelar</button>
          <button className="btn-primary" disabled={busy} onClick={submit}>{busy ? 'Criando...' : 'Criar e gerar'}</button>
        </div>
      </div>
    </div>
  )
}
