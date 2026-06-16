import { useEffect, useState } from 'react'
import { api } from '../api'
import { useWs } from '../App.jsx'
import ApprovalCard from '../components/ApprovalCard.jsx'

export default function Approvals() {
  const [jobs, setJobs] = useState([])
  const [loading, setLoading] = useState(true)
  const { count } = useWs()

  const load = () => api.get('/jobs/approvals').then((d) => { setJobs(d.jobs || []); setLoading(false) }).catch(() => setLoading(false))
  useEffect(() => { load() }, [])
  useEffect(() => { const t = setTimeout(load, 1000); return () => clearTimeout(t) }, [count])

  return (
    <div className="space-y-4 fade-in">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="heading text-2xl font-bold text-gradient">
            Aprovações{' '}
            <span className="badge" style={{ background: 'rgba(124,106,255,0.15)', color: 'var(--accent)' }}>
              {jobs.length}
            </span>
          </h2>
          <p className="text-sm text-text-muted mt-1">Vídeos prontos aguardando revisão humana antes de publicar.</p>
        </div>
      </div>

      {loading ? (
        <div className="space-y-3">
          <div className="card skeleton h-32" />
          <div className="card skeleton h-32" />
          <div className="card skeleton h-32" />
        </div>
      ) : jobs.length === 0 ? (
        <div className="card p-12 text-center">
          <div className="text-5xl mb-4">🎉</div>
          <h3 className="heading font-semibold text-lg mb-1">Nenhum vídeo pendente</h3>
          <p className="text-text-muted text-sm">Todos aprovados ou a fila ainda está processando.</p>
        </div>
      ) : (
        <div className="space-y-3">{jobs.map((j) => <ApprovalCard key={j.id} job={j} onDone={load} />)}</div>
      )}
    </div>
  )
}
