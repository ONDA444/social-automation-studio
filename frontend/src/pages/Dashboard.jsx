import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../api'
import { useWs } from '../App.jsx'
import VideoCard from '../components/VideoCard.jsx'
import AgentLog from '../components/AgentLog.jsx'
import AddJobModal from '../components/AddJobModal.jsx'
import { fmtNum } from '../lib'

export default function Dashboard() {
  const [data, setData] = useState(null)
  const [trending, setTrending] = useState([])
  const [modal, setModal] = useState(false)
  const { count } = useWs()
  const nav = useNavigate()

  const load = () => api.get('/dashboard').then(setData).catch(() => setData({ error: true }))
  useEffect(() => { load(); api.get('/dashboard/trending?niche=entretenimento').then((d) => setTrending(d.suggestions || [])).catch(() => {}) }, [])
  // Refresh job cards when pipeline events arrive.
  useEffect(() => { const t = setTimeout(load, 800); return () => clearTimeout(t) }, [count])

  if (!data) return (
    <div className="space-y-6">
      <div className="h-8 w-40 skeleton" />
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        {Array.from({ length: 4 }).map((_, i) => <div key={i} className="card p-4"><div className="h-20 skeleton" /></div>)}
      </div>
      <div className="grid grid-cols-2 md:grid-cols-3 gap-3">
        {Array.from({ length: 6 }).map((_, i) => <div key={i} className="h-44 skeleton" />)}
      </div>
    </div>
  )
  if (data.error) return <p className="text-error">Backend offline. Inicie a API em :8000.</p>

  const sc = data.status_counts || {}
  const stats = [
    { label: 'Jobs ativos', value: data.active_jobs || 0, icon: '⚙️' },
    { label: 'Aguardando aprovação', value: sc.awaiting_approval || 0, icon: '✅' },
    { label: 'Publicados', value: sc.published || 0, icon: '🚀' },
    { label: 'Views totais', value: fmtNum(data.total_views), icon: '👁' },
  ]

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h2 className="heading text-2xl font-semibold">Dashboard</h2>
        <button className="btn-primary" onClick={() => setModal(true)}>+ Novo vídeo</button>
      </div>

      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        {stats.map((s) => (
          <div key={s.label} className="card card-hover p-4">
            <div className="flex items-center justify-between">
              <span className="w-10 h-10 rounded-btn bg-accent/10 grid place-items-center text-xl">{s.icon}</span>
              <span className="heading text-2xl font-bold tabular-nums">{s.value}</span>
            </div>
            <p className="text-xs text-text-muted mt-2">{s.label}</p>
          </div>
        ))}
      </div>

      <div className="grid grid-cols-1 xl:grid-cols-3 gap-6">
        <div className="xl:col-span-2 space-y-3">
          <h3 className="heading font-semibold">Vídeos recentes</h3>
          {(data.recent_jobs || []).length === 0 && <p className="text-text-muted text-sm">Nenhum vídeo ainda. Clique em “Novo vídeo”.</p>}
          <div className="grid grid-cols-2 md:grid-cols-3 gap-3">
            {(data.recent_jobs || []).map((j) => <VideoCard key={j.id} job={j} onClick={() => nav('/queue')} />)}
          </div>
        </div>
        <div className="h-[480px]"><AgentLog /></div>
      </div>

      {trending.length > 0 && (
        <div className="card p-5">
          <h3 className="heading font-semibold mb-3">💡 Sugestões do dia</h3>
          <div className="flex flex-wrap gap-2">
            {trending.slice(0, 8).map((t, i) => (
              <button key={i} className="badge bg-elevated text-text-primary hover:bg-accent hover:text-white transition-colors" onClick={() => setModal(true)} title={t.reason}>
                {t.topic}
              </button>
            ))}
          </div>
        </div>
      )}

      <AddJobModal open={modal} onClose={() => setModal(false)} onCreated={load} />
    </div>
  )
}
