import { useMemo, useState } from 'react'
import { api } from '../api'
import { useAccounts } from '../AccountsContext.jsx'
import PlatformCard, { remainingUploads } from '../components/PlatformCard.jsx'
import { PageHeader, SectionCard, EmptyState, ErrorBanner } from '../components/ui.jsx'
import { PLATFORM_META } from '../lib'

const PLATFORMS = ['youtube', 'tiktok', 'instagram']

export default function Platforms() {
  const { accounts, loading, error: loadError, refresh: load } = useAccounts()
  const [adding, setAdding] = useState(false)
  const [creating, setCreating] = useState(false)
  const [form, setForm] = useState({ platform: 'youtube', display_name: '', niche: '' })

  const totals = useMemo(() => ({
    all: accounts.length,
    active: accounts.filter((a) => a.status === 'active').length,
    blocked: accounts.filter((a) => a.status === 'quota_exceeded' || remainingUploads(a) === 0).length,
  }), [accounts])

  const create = async () => {
    if (!form.display_name.trim()) return alert('Informe o nome do canal/conta')
    if (creating) return
    setCreating(true)
    try {
      await api.post('/accounts', form)
      setForm({ platform: 'youtube', display_name: '', niche: '' })
      setAdding(false)
      load()
    } catch (err) {
      alert(err?.message || 'Falha ao criar canal/conta')
    } finally {
      setCreating(false)
    }
  }

  return (
    <div className="space-y-4 sm:space-y-5 fade-in">
      <PageHeader title="Plataformas & Canais" sub="Conexoes, quota e padroes de publicacao em uma visao compacta.">
        <button className="btn btn-primary w-full sm:w-auto" onClick={() => setAdding((a) => !a)}>
          {adding ? 'Fechar' : 'Conectar canal'}
        </button>
      </PageHeader>

      {loadError && (
        <ErrorBanner message="Backend offline — não foi possível carregar os canais." onRetry={load} />
      )}

      <div className="grid grid-cols-3 gap-2 sm:gap-3">
        <div className="card p-3 sm:p-4">
          <p className="text-[10px] sm:text-xs font-semibold uppercase" style={{ color: 'var(--text-muted)', letterSpacing: '.06em' }}>Canais</p>
          <p className="data text-xl sm:text-2xl font-black mt-1">{totals.all}</p>
        </div>
        <div className="card p-3 sm:p-4">
          <p className="text-[10px] sm:text-xs font-semibold uppercase" style={{ color: 'var(--text-muted)', letterSpacing: '.06em' }}>Ativos</p>
          <p className="data text-xl sm:text-2xl font-black mt-1" style={{ color: 'var(--success)' }}>{totals.active}</p>
        </div>
        <div className="card p-3 sm:p-4">
          <p className="text-[10px] sm:text-xs font-semibold uppercase truncate" style={{ color: 'var(--text-muted)', letterSpacing: '.06em' }}>Quota</p>
          <p className="data text-xl sm:text-2xl font-black mt-1" style={{ color: totals.blocked ? 'var(--warning)' : 'var(--text-primary)' }}>{totals.blocked}</p>
        </div>
      </div>

      {adding && (
        <SectionCard className="grid sm:grid-cols-2 xl:grid-cols-[180px_minmax(0,1fr)_minmax(0,1fr)_150px] gap-3 items-end slide-down">
          <div>
            <label className="text-sm font-semibold" style={{ color: 'var(--text-muted)' }}>Plataforma</label>
            <select className="input mt-1" value={form.platform} onChange={(e) => setForm({ ...form, platform: e.target.value })}>
              <option value="youtube">YouTube</option>
              <option value="tiktok">TikTok</option>
              <option value="instagram">Instagram</option>
            </select>
          </div>
          <div>
            <label className="text-sm font-semibold" style={{ color: 'var(--text-muted)' }}>Nome do canal</label>
            <input className="input mt-1" value={form.display_name} onChange={(e) => setForm({ ...form, display_name: e.target.value })} />
          </div>
          <div>
            <label className="text-sm font-semibold" style={{ color: 'var(--text-muted)' }}>Nicho</label>
            <input className="input mt-1" value={form.niche} onChange={(e) => setForm({ ...form, niche: e.target.value })} />
          </div>
          <button className="btn btn-primary w-full" onClick={create} disabled={creating}>{creating ? 'Criando...' : 'Criar canal'}</button>
        </SectionCard>
      )}

      {loading ? (
        <div className="space-y-3">
          {Array.from({ length: 2 }).map((_, i) => <div key={i} className="skeleton h-32 rounded-card" />)}
        </div>
      ) : (
        <>
          {PLATFORMS.map((plat) => {
            const list = accounts.filter((a) => a.platform === plat)
            if (list.length === 0) return null
            const meta = PLATFORM_META[plat] || { label: plat, icon: plat.slice(0, 2).toUpperCase() }
            return (
              <section key={plat} className="space-y-3">
                <div className="flex items-center gap-2 sm:gap-3">
                  <span className="data text-sm font-black px-2 py-1 rounded-btn" style={{ background: 'var(--bg-elevated)', color: meta.color }}>
                    {meta.icon}
                  </span>
                  <h3 className="heading text-base sm:text-lg font-bold">{meta.label}</h3>
                  <span className="badge" style={{ background: 'var(--accent-dim)', color: 'var(--accent)' }}>{list.length}</span>
                  <div className="flex-1 h-px" style={{ background: 'var(--border-glass)' }} />
                </div>
                <div className="grid lg:grid-cols-2 gap-3">
                  {list.map((a) => <PlatformCard key={a.id} account={a} onChange={load} />)}
                </div>
              </section>
            )
          })}

          {(() => {
            const others = accounts.filter((a) => !PLATFORMS.includes(a.platform))
            if (others.length === 0) return null
            const meta = { label: 'Outras plataformas', icon: '?', color: 'var(--text-muted)' }
            return (
              <section className="space-y-3">
                <div className="flex items-center gap-2 sm:gap-3">
                  <span className="data text-sm font-black px-2 py-1 rounded-btn" style={{ background: 'var(--bg-elevated)', color: meta.color }}>
                    {meta.icon}
                  </span>
                  <h3 className="heading text-base sm:text-lg font-bold">{meta.label}</h3>
                  <span className="badge" style={{ background: 'var(--accent-dim)', color: 'var(--accent)' }}>{others.length}</span>
                  <div className="flex-1 h-px" style={{ background: 'var(--border-glass)' }} />
                </div>
                <div className="grid lg:grid-cols-2 gap-3">
                  {others.map((a) => <PlatformCard key={a.id} account={a} onChange={load} />)}
                </div>
              </section>
            )
          })()}

          {accounts.length === 0 && (
            <div className="rounded-card" style={{ background: 'var(--bg-surface)', border: '1px solid var(--border)' }}>
              {loadError
                ? <EmptyState
                    icon="⚠"
                    title="Não foi possível carregar os canais."
                    hint="Verifique se o backend está no ar e tente novamente."
                    action={<button className="btn btn-primary" onClick={load}>↻ Tentar novamente</button>}
                  />
                : <EmptyState
                    icon="CH"
                    title="Nenhum canal conectado"
                    hint="Conecte um canal para comecar a publicar."
                    action={<button className="btn btn-primary" onClick={() => setAdding(true)}>Conectar canal</button>}
                  />}
            </div>
          )}
        </>
      )}
    </div>
  )
}
