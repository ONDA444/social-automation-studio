import { useEffect, useState } from 'react'
import { api } from '../api'
import PlatformCard from '../components/PlatformCard.jsx'

const PLATFORMS = ['youtube', 'tiktok', 'instagram']

export default function Platforms() {
  const [accounts, setAccounts] = useState([])
  const [adding, setAdding] = useState(false)
  const [form, setForm] = useState({ platform: 'youtube', display_name: '', niche: '' })

  const load = () => api.get('/accounts').then((d) => setAccounts(d.accounts)).catch(() => {})
  useEffect(() => { load() }, [])

  const create = async () => {
    if (!form.display_name.trim()) return alert('Informe o nome do canal/conta')
    await api.post('/accounts', form)
    setForm({ platform: 'youtube', display_name: '', niche: '' }); setAdding(false); load()
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h2 className="heading text-2xl font-semibold">Plataformas & Workspaces</h2>
        <button className="btn-primary" onClick={() => setAdding((a) => !a)}>+ Conectar conta</button>
      </div>

      {adding && (
        <div className="card p-5 grid md:grid-cols-4 gap-3 items-end">
          <div>
            <label className="text-xs text-text-muted">Plataforma</label>
            <select className="input mt-1" value={form.platform} onChange={(e) => setForm({ ...form, platform: e.target.value })}>
              {PLATFORMS.map((p) => <option key={p} value={p}>{p}</option>)}
            </select>
          </div>
          <div><label className="text-xs text-text-muted">Nome do canal</label><input className="input mt-1" value={form.display_name} onChange={(e) => setForm({ ...form, display_name: e.target.value })} /></div>
          <div><label className="text-xs text-text-muted">Nicho</label><input className="input mt-1" value={form.niche} onChange={(e) => setForm({ ...form, niche: e.target.value })} /></div>
          <button className="btn-primary" onClick={create}>Criar</button>
        </div>
      )}

      {PLATFORMS.map((plat) => {
        const list = accounts.filter((a) => a.platform === plat)
        if (list.length === 0) return null
        return (
          <div key={plat}>
            <h3 className="heading font-semibold capitalize mb-2">{plat}</h3>
            <div className="grid md:grid-cols-2 lg:grid-cols-3 gap-4">
              {list.map((a) => <PlatformCard key={a.id} account={a} onChange={load} />)}
            </div>
          </div>
        )
      })}

      {accounts.length === 0 && <div className="card p-10 text-center text-text-muted">Nenhuma conta. Clique em “Conectar conta”.</div>}
    </div>
  )
}
