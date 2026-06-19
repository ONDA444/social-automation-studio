import { useEffect, useState } from 'react'
import { api } from '../api'
import { useWs } from '../App.jsx'
import ApprovalCard from '../components/ApprovalCard.jsx'
import { PageHeader, EmptyState } from '../components/ui.jsx'

export default function Approvals() {
  const [jobs, setJobs] = useState([])
  const [loading, setLoading] = useState(true)
  const { count } = useWs()

  const load = () => api.get('/jobs/approvals').then((d) => { setJobs(d.jobs || []); setLoading(false) }).catch(() => setLoading(false))
  useEffect(() => { load() }, [])
  useEffect(() => { const t = setTimeout(load, 1000); return () => clearTimeout(t) }, [count])

  return (
    <div className="space-y-4 fade-in">
      <PageHeader
        title={
          <span className="inline-flex items-center gap-2.5">
            Aprovações
            <span className="badge" style={{ background: 'var(--accent-dim)', color: 'var(--accent)', border: '1px solid var(--border)' }}>{jobs.length}</span>
          </span>
        }
        sub="Vídeos prontos aguardando revisão humana antes de publicar."
      />

      {loading ? (
        <div className="space-y-3">
          {Array.from({ length: 3 }).map((_, i) => <div key={i} className="skeleton h-32 rounded-card" />)}
        </div>
      ) : jobs.length === 0 ? (
        <div className="rounded-card" style={{ background: 'var(--bg-surface)', border: '1px solid var(--border)' }}>
          <EmptyState icon="🎉" title="Nenhum vídeo pendente"
            hint="Todos aprovados ou a fila ainda está processando." />
        </div>
      ) : (
        <div className="space-y-3">{jobs.map((j) => <ApprovalCard key={j.id} job={j} onDone={load} />)}</div>
      )}
    </div>
  )
}
