import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../api'
import { useWs, useDashboardStatus } from '../App.jsx'
import VideoCard from '../components/VideoCard.jsx'
import AgentLog from '../components/AgentLog.jsx'
import AddJobModal from '../components/AddJobModal.jsx'
import CommandCenter from '../components/CommandCenter.jsx'
import { PageHeader, SectionCard, StatTile, EmptyState } from '../components/ui.jsx'
import { fmtNum } from '../lib'

export default function Dashboard() {
  const [trending, setTrending] = useState([])
  const [modal, setModal] = useState(false)
  const { connected } = useWs()
  // Shared with TopBar (also polls /dashboard) so the two don't run separate
  // pollers hitting the same endpoint while this page is open.
  const { data, stale, refresh } = useDashboardStatus()
  const nav = useNavigate()

  useEffect(() => {
    api.get('/dashboard/trending?niche=entretenimento')
      .then((d) => setTrending(d.suggestions || []))
      .catch(() => {})
  }, [])

  /* ── Loading skeleton ── */
  if (!data) return (
    <div className="space-y-6">
      <div className="h-8 w-48 skeleton rounded-xl" />
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
        {Array.from({ length: 4 }).map((_, i) => <div key={i} className="h-24 skeleton rounded-card" />)}
      </div>
      <div className="grid grid-cols-2 md:grid-cols-3 gap-3">
        {Array.from({ length: 6 }).map((_, i) => <div key={i} className="h-44 skeleton rounded-card" />)}
      </div>
    </div>
  )

  if (data.error) return (
    <div className="rounded-card p-6 text-sm"
      style={{ background: 'rgba(255,80,102,0.08)', border: '1px solid rgba(255,80,102,0.25)', color: 'var(--error)' }}>
      Backend offline. Inicie a API em :8000.
    </div>
  )

  const sc = data.status_counts || {}
  const stats = [
    { label: 'Jobs ativos', value: data.active_jobs || 0, icon: '⚙️', accent: 'var(--accent)', to: '/queue' },
    { label: 'Aguardando aprovação', value: sc.awaiting_approval || 0, icon: '✅', accent: 'var(--warning)', to: '/approvals' },
    { label: 'Publicados', value: sc.published || 0, icon: '🚀', accent: 'var(--success)', to: '/queue' },
    { label: 'Views totais', value: data.total_views || 0, icon: '👁', accent: 'var(--text-primary)', to: '/analytics', format: fmtNum },
  ]

  const today = new Date().toLocaleDateString('pt-BR', { weekday: 'long', year: 'numeric', month: 'long', day: 'numeric' })

  return (
    <div className="space-y-6 fade-in">

      <PageHeader title="Dashboard" sub={today.charAt(0).toUpperCase() + today.slice(1)}>
        <button className="btn btn-primary" onClick={() => setModal(true)}>+ Novo vídeo</button>
      </PageHeader>

      {stale && (
        <div className="rounded-card px-4 py-2.5 text-sm"
          style={{ background: 'rgba(255,80,102,0.08)', border: '1px solid rgba(255,80,102,0.25)', color: 'var(--error)' }}>
          ⚠️ Backend offline — exibindo os últimos dados conhecidos. Tentando reconectar…
        </div>
      )}

      {/* Stat tiles */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3 stagger">
        {stats.map((s) => (
          <StatTile key={s.label} label={s.label} value={s.value} format={s.format}
            accent={s.accent} icon={s.icon} onClick={() => nav(s.to)} />
        ))}
      </div>

      {/* Command Center — status ao vivo do sistema */}
      <CommandCenter />

      {/* Recent videos + live agent */}
      <div className="grid grid-cols-1 xl:grid-cols-3 gap-5">
        <div className="xl:col-span-2">
          <SectionCard title="Vídeos recentes" sub="últimos gerados">
            {(data.recent_jobs || []).length === 0
              ? <EmptyState icon="🎬" title='Nenhum vídeo ainda.'
                  hint='Clique em "+ Novo vídeo" para gerar o primeiro.'
                  action={<button className="btn btn-primary text-sm" onClick={() => setModal(true)}>+ Novo vídeo</button>} />
              : (
                <div className="grid grid-cols-2 md:grid-cols-3 gap-3 stagger">
                  {(data.recent_jobs || []).map((j) => (
                    <VideoCard key={j.id} job={j} onClick={() => nav('/queue')} />
                  ))}
                </div>
              )}
          </SectionCard>
        </div>

        <div className="rounded-card flex flex-col h-[480px] overflow-hidden"
          style={{ background: 'var(--bg-surface)', border: '1px solid var(--border)' }}>
          <div className="flex items-center gap-2 px-4 py-3 shrink-0" style={{ borderBottom: '1px solid rgba(255,255,255,0.06)' }}>
            <span className="relative flex h-2 w-2">
              {connected && <span className="absolute inline-flex h-full w-full rounded-full opacity-75 animate-ping" style={{ background: 'var(--success)' }} />}
              <span className="relative inline-flex rounded-full h-2 w-2" style={{ background: connected ? 'var(--success)' : 'var(--text-muted)' }} />
            </span>
            <span className="text-sm font-semibold">Agente ao vivo</span>
          </div>
          <div className="flex-1 overflow-hidden"><AgentLog /></div>
        </div>
      </div>

      {/* Trending suggestions */}
      {trending.length > 0 && (
        <SectionCard title="💡 Sugestões do dia" sub="temas em alta">
          <div className="flex flex-wrap gap-2">
            {trending.slice(0, 8).map((t, i) => (
              <button key={i} onClick={() => setModal(true)} title={t.reason}
                className="px-3 py-1.5 rounded-pill text-xs font-medium transition-colors hover:brightness-125"
                style={{ background: 'var(--accent-dim)', border: '1px solid var(--border)', color: 'var(--accent)' }}>
                {t.topic}
              </button>
            ))}
          </div>
        </SectionCard>
      )}

      <AddJobModal open={modal} onClose={() => setModal(false)} onCreated={refresh} />
    </div>
  )
}
