import { useEffect, useRef, useState } from 'react'
import { DndContext, closestCenter, PointerSensor, useSensor, useSensors } from '@dnd-kit/core'
import { SortableContext, useSortable, verticalListSortingStrategy, arrayMove } from '@dnd-kit/sortable'
import { CSS } from '@dnd-kit/utilities'
import { api } from '../api'
import { useWs } from '../App.jsx'
import AddJobModal from '../components/AddJobModal.jsx'
import { statusMeta, PLATFORM_META, fmtDate } from '../lib'

function Row({ job, onRetry, onDelete }) {
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } = useSortable({ id: job.id })
  const s = statusMeta(job.status)
  return (
    <div ref={setNodeRef} style={{ transform: CSS.Transform.toString(transform), transition, opacity: isDragging ? 0.5 : 1 }}
      className="card p-3 flex items-center gap-3">
      <span {...attributes} {...listeners} className="cursor-grab text-text-muted px-1 select-none">⠿</span>
      <div className="flex-1 min-w-0">
        <p className="font-medium truncate">{job.title}</p>
        <div className="flex items-center gap-2 mt-0.5">
          {(job.target_platforms || []).map((p) => { const m = PLATFORM_META[p]; return m ? <span key={p} className="text-[10px]" style={{ color: m.color }}>{m.icon}</span> : null })}
          <span className="text-[11px] text-text-muted">{job.content_type} · {fmtDate(job.created_at)}</span>
        </div>
      </div>
      {job.status === 'processing' && (
        <div className="w-28">
          <div className="h-1.5 rounded-full bg-elevated overflow-hidden"><div className="h-full bg-accent" style={{ width: `${job.progress}%` }} /></div>
          <p className="text-[10px] text-text-muted mt-0.5 truncate">{job.current_agent}</p>
        </div>
      )}
      <span className="badge shrink-0" style={{ background: s.color + '22', color: s.color }}>{s.label}</span>
      {job.status === 'error' && <button className="btn-ghost text-xs" onClick={() => onRetry(job.id)}>↻</button>}
      <button className="btn-ghost text-xs" onClick={() => onDelete(job.id)}>🗑</button>
    </div>
  )
}

export default function Queue() {
  const [jobs, setJobs] = useState([])
  const [modal, setModal] = useState(false)
  const fileRef = useRef(null)
  const { events } = useWs()
  const sensors = useSensors(useSensor(PointerSensor, { activationConstraint: { distance: 5 } }))

  const load = () => api.get('/jobs?limit=200').then((d) => setJobs(d.jobs)).catch(() => {})
  useEffect(() => { load() }, [])
  useEffect(() => { const t = setTimeout(load, 800); return () => clearTimeout(t) }, [events.length])

  const onDragEnd = (e) => {
    const { active, over } = e
    if (over && active.id !== over.id) {
      setJobs((j) => arrayMove(j, j.findIndex((x) => x.id === active.id), j.findIndex((x) => x.id === over.id)))
    }
  }
  const retry = async (id) => { await api.post(`/jobs/${id}/retry`); load() }
  const del = async (id) => { if (confirm('Remover job?')) { await api.del(`/jobs/${id}`); load() } }
  const importCsv = async (e) => {
    const f = e.target.files?.[0]; if (!f) return
    try { const r = await api.upload('/jobs/import-csv', f); alert(`${r.count} jobs criados`); load() }
    catch (err) { alert(err.message) }
    e.target.value = ''
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h2 className="heading text-2xl font-semibold">Fila ({jobs.length})</h2>
        <div className="flex gap-2">
          <button className="btn-ghost" onClick={() => fileRef.current?.click()}>⬆ Importar CSV</button>
          <input ref={fileRef} type="file" accept=".csv" className="hidden" onChange={importCsv} />
          <button className="btn-primary" onClick={() => setModal(true)}>+ Novo vídeo</button>
        </div>
      </div>
      <p className="text-xs text-text-muted">CSV: <code className="font-mono">title, topic, content_type, mode, target_platforms (a|b), account_id</code></p>

      {jobs.length === 0 ? (
        <div className="card p-10 text-center text-text-muted">Fila vazia. Crie um vídeo ou importe um CSV.</div>
      ) : (
        <DndContext sensors={sensors} collisionDetection={closestCenter} onDragEnd={onDragEnd}>
          <SortableContext items={jobs.map((j) => j.id)} strategy={verticalListSortingStrategy}>
            <div className="space-y-2">{jobs.map((j) => <Row key={j.id} job={j} onRetry={retry} onDelete={del} />)}</div>
          </SortableContext>
        </DndContext>
      )}

      <AddJobModal open={modal} onClose={() => setModal(false)} onCreated={load} />
    </div>
  )
}
