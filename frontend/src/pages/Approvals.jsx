import { useEffect, useState } from 'react'
import { api } from '../api'
import { useWs } from '../App.jsx'
import ApprovalCard from '../components/ApprovalCard.jsx'

export default function Approvals() {
  const [jobs, setJobs] = useState([])
  const [loading, setLoading] = useState(true)
  const { events } = useWs()

  const load = () => api.get('/jobs/approvals').then((d) => { setJobs(d.jobs); setLoading(false) }).catch(() => setLoading(false))
  useEffect(() => { load() }, [])
  useEffect(() => { const t = setTimeout(load, 1000); return () => clearTimeout(t) }, [events.length])

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h2 className="heading text-2xl font-semibold">Aprovações ({jobs.length})</h2>
      </div>
      <p className="text-sm text-text-muted">Vídeos prontos aguardando revisão humana antes de publicar.</p>

      {loading ? <p className="text-text-muted">Carregando…</p> :
        jobs.length === 0 ? (
          <div className="card p-10 text-center text-text-muted">Nenhum vídeo aguardando aprovação. 🎉</div>
        ) : (
          <div className="space-y-3">{jobs.map((j) => <ApprovalCard key={j.id} job={j} onDone={load} />)}</div>
        )}
    </div>
  )
}
