import { useEffect, useRef, useState } from 'react'
import { DndContext, closestCenter, PointerSensor, useSensor, useSensors } from '@dnd-kit/core'
import { SortableContext, useSortable, verticalListSortingStrategy, arrayMove } from '@dnd-kit/sortable'
import { CSS } from '@dnd-kit/utilities'
import { api, mediaUrl } from '../api'
import { useWs } from '../App.jsx'
import AddJobModal from '../components/AddJobModal.jsx'
import { statusMeta, PLATFORM_META, fmtDate } from '../lib'

// Status que permitem trocar de canal manualmente.
const CHANNEL_EDITABLE = new Set(['queued', 'awaiting_approval', 'approved', 'error'])

function Row({ job, accounts, selected, onToggleSelect, onChannelChange, onRetry, onRepublish, onDelete }) {
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } = useSortable({ id: job.id })
  const [showPlayer, setShowPlayer] = useState(false)
  const s = statusMeta(job.status)
  const canEditChannel = CHANNEL_EDITABLE.has(job.status)
  const canRepublish = job.status === 'approved' || job.status === 'error'
  return (
    <div ref={setNodeRef} style={{ transform: CSS.Transform.toString(transform), transition, opacity: isDragging ? 0.5 : 1 }}
      className="card p-3">
      <div className="flex items-center gap-3">
        <input type="checkbox" className="shrink-0 accent-accent" checked={selected} onChange={() => onToggleSelect(job.id)} />
        <span {...attributes} {...listeners} className="cursor-grab text-text-muted px-1 select-none">⠿</span>
        <div className="flex-1 min-w-0">
          <p className="font-medium truncate">{job.title}</p>
          <div className="flex items-center gap-2 mt-0.5">
            {(job.target_platforms || []).map((p) => { const m = PLATFORM_META[p]; return m ? <span key={p} className="text-[10px]" style={{ color: m.color }}>{m.icon}</span> : null })}
            <span className="text-[11px] text-text-muted">{job.content_type} · {fmtDate(job.created_at)}</span>
          </div>
          {job.status === 'error' && job.error_message && (
            <p className="text-[11px] mt-0.5 truncate" style={{ color: 'var(--error)' }} title={job.error_message}>{job.error_message}</p>
          )}
        </div>
        <select
          className="input text-xs py-1 px-2 w-32 shrink-0"
          value={job.account_id ?? ''}
          disabled={!canEditChannel}
          title={canEditChannel ? 'Trocar canal' : 'Canal não editável neste status'}
          onChange={(e) => onChannelChange(job.id, e.target.value)}
        >
          <option value="">-- sem conta --</option>
          {accounts.map((a) => (
            <option key={a.id} value={a.id}>{a.display_name}{a.platform ? ` (${a.platform})` : ''}</option>
          ))}
        </select>
        {job.status === 'processing' && (
          <div className="w-28">
            <div className="h-1.5 rounded-full bg-elevated overflow-hidden"><div className="h-full bg-accent" style={{ width: `${job.progress || 0}%` }} /></div>
            <p className="text-[10px] text-text-muted mt-0.5 truncate">{job.current_agent}</p>
          </div>
        )}
        <span className="badge shrink-0" style={{ background: s.color + '22', color: s.color }}>{s.label}</span>
        {job.main_video_path && <button className="btn-ghost text-xs" onClick={() => setShowPlayer((v) => !v)}>{showPlayer ? 'Ocultar' : '▶ Ver'}</button>}
        {canRepublish && <button className="btn-ghost text-xs" onClick={() => onRepublish(job.id)} title="Republicar">⤴ Republicar</button>}
        {job.status === 'error' && <button className="btn-ghost text-xs" onClick={() => onRetry(job.id)}>↻</button>}
        <button className="btn-ghost text-xs" onClick={() => onDelete(job.id)}>🗑</button>
      </div>
      {showPlayer && job.main_video_path && (
        <video src={mediaUrl(job.main_video_path)} controls className="w-full rounded mt-2" />
      )}
    </div>
  )
}

export default function Queue() {
  const [jobs, setJobs] = useState([])
  const [accounts, setAccounts] = useState([])
  const [selected, setSelected] = useState(() => new Set())
  const [modal, setModal] = useState(false)
  const fileRef = useRef(null)
  const { count } = useWs()
  const sensors = useSensors(useSensor(PointerSensor, { activationConstraint: { distance: 5 } }))

  const load = () => api.get('/jobs?limit=200').then((d) => setJobs(d.jobs || [])).catch(() => {})
  useEffect(() => { load() }, [])
  useEffect(() => { const t = setTimeout(load, 800); return () => clearTimeout(t) }, [count])
  // Carrega as contas uma vez para o seletor de canal.
  useEffect(() => { api.get('/accounts').then((d) => setAccounts(d.accounts || [])).catch(() => setAccounts([])) }, [])

  // Mantém a seleção coerente com os jobs ainda existentes.
  useEffect(() => {
    setSelected((prev) => {
      if (prev.size === 0) return prev
      const ids = new Set(jobs.map((j) => j.id))
      const next = new Set([...prev].filter((id) => ids.has(id)))
      return next.size === prev.size ? prev : next
    })
  }, [jobs])

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

  const changeChannel = async (id, val) => {
    try {
      await api.patch(`/jobs/${id}`, { account_id: val ? Number(val) : null })
      load()
    } catch (err) { alert(err.message) }
  }

  const republish = async (id) => {
    try {
      const r = await api.post(`/jobs/${id}/publish`)
      const parts = []
      if (r?.note) parts.push(r.note)
      if (r?.pending_platforms?.length) parts.push(`Pendentes: ${r.pending_platforms.join(', ')}`)
      alert(parts.length ? parts.join('\n') : 'Republicação iniciada.')
      load()
    } catch (err) { alert(err.message) }
  }

  // ---- seleção múltipla ----
  const toggleSelect = (id) => {
    setSelected((prev) => {
      const next = new Set(prev)
      next.has(id) ? next.delete(id) : next.add(id)
      return next
    })
  }
  const allSelected = jobs.length > 0 && selected.size === jobs.length
  const toggleSelectAll = () => {
    setSelected(allSelected ? new Set() : new Set(jobs.map((j) => j.id)))
  }
  const deleteSelected = async () => {
    const ids = [...selected]
    if (ids.length === 0) return
    if (!confirm(`Excluir ${ids.length} job(s) selecionado(s)?`)) return
    try {
      await api.post('/jobs/bulk-delete', { ids })
      setSelected(new Set())
      load()
    } catch (err) { alert(err.message) }
  }
  const clearErrors = async () => {
    if (!confirm('Excluir todos os jobs com erro?')) return
    try {
      await api.post('/jobs/bulk-delete', { status: 'error' })
      setSelected(new Set())
      load()
    } catch (err) { alert(err.message) }
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

      {jobs.length > 0 && (
        <div className="card p-2 flex items-center gap-3 flex-wrap">
          <label className="flex items-center gap-2 text-xs text-text-muted cursor-pointer select-none">
            <input type="checkbox" className="accent-accent" checked={allSelected} onChange={toggleSelectAll} />
            Selecionar todos
          </label>
          <span className="text-xs text-text-muted">{selected.size} selecionado(s)</span>
          <div className="flex-1" />
          <button className="btn-ghost text-xs" disabled={selected.size === 0} onClick={deleteSelected}>🗑 Excluir selecionados</button>
          <button className="btn-ghost text-xs" onClick={clearErrors}>🧹 Limpar erros</button>
        </div>
      )}

      {jobs.length === 0 ? (
        <div className="card p-10 text-center text-text-muted">Fila vazia. Crie um vídeo ou importe um CSV.</div>
      ) : (
        <DndContext sensors={sensors} collisionDetection={closestCenter} onDragEnd={onDragEnd}>
          <SortableContext items={jobs.map((j) => j.id)} strategy={verticalListSortingStrategy}>
            <div className="space-y-2">{jobs.map((j) => (
              <Row
                key={j.id}
                job={j}
                accounts={accounts}
                selected={selected.has(j.id)}
                onToggleSelect={toggleSelect}
                onChannelChange={changeChannel}
                onRetry={retry}
                onRepublish={republish}
                onDelete={del}
              />
            ))}</div>
          </SortableContext>
        </DndContext>
      )}

      <AddJobModal open={modal} onClose={() => setModal(false)} onCreated={load} />
    </div>
  )
}
