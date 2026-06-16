import { useEffect, useRef, useState } from 'react'
import { DndContext, closestCenter, PointerSensor, useSensor, useSensors } from '@dnd-kit/core'
import { SortableContext, useSortable, verticalListSortingStrategy, arrayMove } from '@dnd-kit/sortable'
import { CSS } from '@dnd-kit/utilities'
import { api, mediaUrl } from '../api'
import { useWs } from '../App.jsx'
import AddJobModal from '../components/AddJobModal.jsx'
import { statusMeta, PLATFORM_META, fmtDate } from '../lib'

const CHANNEL_EDITABLE = new Set(['queued', 'awaiting_approval', 'approved', 'error'])

function Row({ job, accounts, selected, onToggleSelect, onChannelChange, onRetry, onRepublish, onDelete }) {
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } = useSortable({ id: job.id })
  const [showPlayer, setShowPlayer] = useState(false)
  const [showFullError, setShowFullError] = useState(false)
  const s = statusMeta(job.status)
  const canEditChannel = CHANNEL_EDITABLE.has(job.status)
  const canRepublish   = job.status === 'approved' || job.status === 'error'

  const errMsg = job.error_message || ''
  const errShort = errMsg.length > 90 ? errMsg.slice(0, 90) + '…' : errMsg

  return (
    <div
      ref={setNodeRef}
      style={{ transform: CSS.Transform.toString(transform), transition, opacity: isDragging ? 0.5 : 1 }}
      className="card p-3 fade-in">
      {/* Linha principal: seleção, título e status */}
      <div className="flex items-start gap-2 sm:gap-3">
        <input type="checkbox" className="shrink-0 accent-accent mt-1 w-4 h-4" checked={selected} onChange={() => onToggleSelect(job.id)} />
        <span {...attributes} {...listeners} className="cursor-grab text-text-muted px-1 select-none text-lg leading-none mt-0.5 shrink-0">⠿</span>

        <div className="flex-1 min-w-0">
          <p className="font-medium truncate">{job.title}</p>
          <div className="flex items-center gap-2 mt-0.5 flex-wrap">
            {(job.target_platforms || []).map((p) => {
              const m = PLATFORM_META[p]
              return m ? <span key={p} className="text-[10px]" style={{ color: m.color }}>{m.icon}</span> : null
            })}
            <span className="text-[11px] text-text-muted">{job.content_type} · {fmtDate(job.created_at)}</span>
          </div>

          {/* Mensagem de erro expansível */}
          {job.status === 'error' && errMsg && (
            <button
              className="text-left mt-1 w-full"
              onClick={() => setShowFullError((v) => !v)}
              title={showFullError ? 'Clique para recolher' : 'Clique para ver completo'}>
              <p className="text-[11px] leading-snug" style={{ color: 'var(--error)' }}>
                {showFullError ? errMsg : errShort}
              </p>
              {errMsg.length > 90 && (
                <span className="text-[10px]" style={{ color: 'var(--error)', opacity: 0.7 }}>
                  {showFullError ? '▲ recolher' : '▼ ver mais'}
                </span>
              )}
            </button>
          )}
        </div>

        {/* Badge de status — sempre visível na linha de cima */}
        <span className="badge shrink-0" style={{ background: s.color + '22', color: s.color }}>{s.label}</span>
      </div>

      {/* Barra de progresso (full width no mobile) */}
      {job.status === 'processing' && (
        <div className="mt-2.5">
          <div className="h-1.5 rounded-full bg-elevated overflow-hidden">
            <div className="h-full rounded-full transition-all duration-500"
              style={{ width: `${job.progress || 0}%`, background: 'var(--grad-accent)' }} />
          </div>
          <p className="text-[10px] text-text-muted mt-0.5 truncate">{job.current_agent}</p>
        </div>
      )}

      {/* Linha de controles: canal + ações — empilha/encolhe no mobile */}
      <div className="flex flex-wrap items-center gap-2 mt-3 pt-2.5 border-t" style={{ borderColor: 'rgba(255,255,255,0.06)' }}>
        {/* Seletor de canal */}
        <select
          className="input text-xs py-1.5 px-2 w-full sm:w-40 shrink-0"
          value={job.account_id ?? ''}
          disabled={!canEditChannel}
          title={canEditChannel ? 'Trocar canal' : 'Canal não editável neste status'}
          onChange={(e) => onChannelChange(job.id, e.target.value)}>
          <option value="">-- sem conta --</option>
          {accounts.map((a) => (
            <option key={a.id} value={a.id}>{a.display_name}{a.platform ? ` (${a.platform})` : ''}</option>
          ))}
        </select>

        <div className="flex-1 hidden sm:block" />

        {/* Ações */}
        {job.main_video_path && (
          <button className="btn-ghost text-xs shrink-0 min-h-[34px]" onClick={() => setShowPlayer((v) => !v)}>
            {showPlayer ? 'Ocultar' : '▶ Ver'}
          </button>
        )}
        {canRepublish && (
          <button className="btn-ghost text-xs shrink-0 min-h-[34px]" onClick={() => onRepublish(job.id)} title="Republicar">
            ⤴ Republicar
          </button>
        )}
        {job.status === 'error' && (
          <button className="btn-ghost text-xs shrink-0 min-h-[34px] px-3" onClick={() => onRetry(job.id)} title="Tentar novamente">
            ↻
          </button>
        )}
        <button className="btn-ghost text-xs shrink-0 min-h-[34px] px-3" onClick={() => onDelete(job.id)} title="Excluir">🗑</button>
      </div>

      {showPlayer && job.main_video_path && (
        <div className="mt-3 rounded-xl overflow-hidden bg-black flex justify-center" style={{ maxHeight: '60vh' }}>
          <video src={mediaUrl(job.main_video_path)} controls style={{ maxHeight: '60vh', width: 'auto', maxWidth: '100%' }} />
        </div>
      )}
    </div>
  )
}

export default function Queue() {
  const [jobs, setJobs]         = useState([])
  const [accounts, setAccounts] = useState([])
  const [selected, setSelected] = useState(() => new Set())
  const [modal, setModal]       = useState(false)
  const fileRef = useRef(null)
  const { count } = useWs()
  const sensors = useSensors(useSensor(PointerSensor, { activationConstraint: { distance: 5 } }))

  const load = () => api.get('/jobs?limit=200').then((d) => setJobs(d.jobs || [])).catch(() => {})

  useEffect(() => { load() }, [])
  useEffect(() => { const t = setTimeout(load, 800); return () => clearTimeout(t) }, [count])
  useEffect(() => {
    api.get('/accounts').then((d) => setAccounts(d.accounts || [])).catch(() => setAccounts([]))
  }, [])

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
      setJobs((j) => arrayMove(
        j,
        j.findIndex((x) => x.id === active.id),
        j.findIndex((x) => x.id === over.id),
      ))
    }
  }

  const retry = async (id) => {
    try { await api.post(`/jobs/${id}/retry`); load() }
    catch (err) { alert(err.message) }
  }

  const del = async (id) => {
    if (!confirm('Remover job?')) return
    try { await api.del(`/jobs/${id}`); load() }
    catch (err) { alert(err.message) }
  }

  const importCsv = async (e) => {
    const f = e.target.files?.[0]
    if (!f) return
    try { const r = await api.upload('/jobs/import-csv', f); alert(`${r.count} jobs criados`); load() }
    catch (err) { alert(err.message) }
    e.target.value = ''
  }

  const changeChannel = async (id, val) => {
    try { await api.patch(`/jobs/${id}`, { account_id: val ? Number(val) : null }); load() }
    catch (err) { alert(err.message) }
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

  // Seleção múltipla
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
    try { await api.post('/jobs/bulk-delete', { ids }); setSelected(new Set()); load() }
    catch (err) { alert(err.message) }
  }

  const clearErrors = async () => {
    if (!confirm('Excluir todos os jobs com erro?')) return
    try { await api.post('/jobs/bulk-delete', { status: 'error' }); setSelected(new Set()); load() }
    catch (err) { alert(err.message) }
  }

  const errCount = jobs.filter((j) => j.status === 'error').length

  return (
    <div className="space-y-4 fade-in">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-3">
          <h2 className="heading text-2xl font-semibold">Fila</h2>
          <span className="badge" style={{ background: 'rgba(108,92,231,0.15)', color: 'var(--accent)', border: '1px solid rgba(108,92,231,0.25)' }}>
            {jobs.length}
          </span>
          {errCount > 0 && (
            <span className="badge" style={{ background: 'rgba(255,71,87,0.12)', color: 'var(--error)', border: '1px solid rgba(255,71,87,0.2)' }}>
              {errCount} erro{errCount > 1 ? 's' : ''}
            </span>
          )}
        </div>
        <div className="flex gap-2 w-full sm:w-auto">
          <button className="btn-ghost text-sm flex-1 sm:flex-none" onClick={() => fileRef.current?.click()}>⬆ Importar CSV</button>
          <input ref={fileRef} type="file" accept=".csv" className="hidden" onChange={importCsv} />
          <button className="btn-primary text-sm flex-1 sm:flex-none" onClick={() => setModal(true)}>+ Novo vídeo</button>
        </div>
      </div>

      <p className="text-xs text-text-muted">
        CSV: <code className="font-mono bg-elevated px-1.5 py-0.5 rounded">title, topic, content_type, mode, target_platforms (a|b), account_id</code>
      </p>

      {jobs.length > 0 && (
        <div className="card p-2.5 flex items-center gap-3 flex-wrap">
          <label className="flex items-center gap-2 text-xs text-text-muted cursor-pointer select-none">
            <input type="checkbox" className="accent-accent" checked={allSelected} onChange={toggleSelectAll} />
            Selecionar todos
          </label>
          {selected.size > 0 && (
            <span className="text-xs text-text-muted">{selected.size} selecionado(s)</span>
          )}
          <div className="flex-1" />
          <button className="btn-ghost text-xs" disabled={selected.size === 0} onClick={deleteSelected}>
            🗑 Excluir selecionados
          </button>
          {errCount > 0 && (
            <button className="btn-ghost text-xs" onClick={clearErrors}>
              🧹 Limpar {errCount} erro{errCount > 1 ? 's' : ''}
            </button>
          )}
        </div>
      )}

      {jobs.length === 0 ? (
        <div className="card p-12 text-center text-text-muted">
          <p className="text-4xl mb-3">🎬</p>
          <p className="font-medium">Fila vazia.</p>
          <p className="text-sm mt-1">Crie um vídeo ou importe um CSV para começar.</p>
        </div>
      ) : (
        <DndContext sensors={sensors} collisionDetection={closestCenter} onDragEnd={onDragEnd}>
          <SortableContext items={jobs.map((j) => j.id)} strategy={verticalListSortingStrategy}>
            <div className="space-y-2">
              {jobs.map((j) => (
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
              ))}
            </div>
          </SortableContext>
        </DndContext>
      )}

      <AddJobModal open={modal} onClose={() => setModal(false)} onCreated={load} />
    </div>
  )
}
