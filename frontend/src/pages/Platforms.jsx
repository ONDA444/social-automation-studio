import { useEffect, useState } from 'react'
import { api } from '../api'
import PlatformCard from '../components/PlatformCard.jsx'
import { PageHeader, SectionCard, EmptyState } from '../components/ui.jsx'

const PLATFORMS = ['youtube', 'tiktok', 'instagram']

const PLAT_META = {
  youtube: { label: 'YouTube', icon: '🎬' },
  tiktok: { label: 'TikTok', icon: '📱' },
  instagram: { label: 'Instagram', icon: '📸' },
}

export default function Platforms() {
  const [accounts, setAccounts] = useState([])
  const [adding, setAdding] = useState(false)
  const [form, setForm] = useState({ platform: 'youtube', display_name: '', niche: '' })

  const load = () => api.get('/accounts').then((d) => setAccounts(d.accounts || [])).catch(() => {})
  useEffect(() => { load() }, [])

  const create = async () => {
    if (!form.display_name.trim()) return alert('Informe o nome do canal/conta')
    await api.post('/accounts', form)
    setForm({ platform: 'youtube', display_name: '', niche: '' }); setAdding(false); load()
  }

  return (
    <div className="space-y-6 fade-in">
      <PageHeader title="Plataformas & Canais" sub="Conecte e gerencie seus canais de publicação.">
        <button className="btn btn-primary" onClick={() => setAdding((a) => !a)}>+ Conectar canal</button>
      </PageHeader>

      {adding && (
        <SectionCard className="grid md:grid-cols-4 gap-3 items-end slide-down">
          <div>
            <label className="text-xs text-text-muted">Plataforma</label>
            <select className="input mt-1" value={form.platform} onChange={(e) => setForm({ ...form, platform: e.target.value })}>
              <option value="youtube">🎬 YouTube</option>
              <option value="tiktok">📱 TikTok</option>
              <option value="instagram">📸 Instagram</option>
            </select>
          </div>
          <div>
            <label className="text-xs text-text-muted">Nome do canal</label>
            <input className="input mt-1" value={form.display_name} onChange={(e) => setForm({ ...form, display_name: e.target.value })} />
          </div>
          <div>
            <label className="text-xs text-text-muted">Nicho</label>
            <input className="input mt-1" value={form.niche} onChange={(e) => setForm({ ...form, niche: e.target.value })} />
          </div>
          <button className="btn btn-primary" onClick={create}>Criar</button>
        </SectionCard>
      )}

      {PLATFORMS.map((plat) => {
        const list = accounts.filter((a) => a.platform === plat)
        if (list.length === 0) return null
        const meta = PLAT_META[plat]
        return (
          <div key={plat}>
            <div className="flex items-center gap-3 mb-4">
              <span className="text-lg">{meta.icon}</span>
              <h3 className="heading font-semibold">{meta.label}</h3>
              <span className="badge" style={{ background: 'var(--accent-dim)', color: 'var(--accent)', border: '1px solid var(--border)' }}>{list.length}</span>
              <div className="flex-1 h-px" style={{ background: 'rgba(255,255,255,0.06)' }} />
            </div>
            <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5 xl:grid-cols-6 gap-3">
              {list.map((a) => <PlatformCard key={a.id} account={a} onChange={load} onChanged={load} />)}
            </div>
          </div>
        )
      })}

      {accounts.length === 0 && (
        <div className="rounded-card" style={{ background: 'var(--bg-surface)', border: '1px solid var(--border)' }}>
          <EmptyState icon="🔗" title="Nenhum canal conectado"
            hint="Conecte um canal para começar a publicar."
            action={<button className="btn btn-primary" onClick={() => setAdding(true)}>+ Conectar canal</button>} />
        </div>
      )}
    </div>
  )
}
