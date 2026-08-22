import { useEffect, useRef, useState } from 'react'
import { api, mediaUrl } from '../api'
import { useWs } from '../App.jsx'
import { useAccounts } from '../AccountsContext.jsx'
import AddJobModal from '../components/AddJobModal.jsx'
import PipelineTimeline from '../components/PipelineTimeline.jsx'
import { PageHeader, EmptyState, ErrorBanner, StatusBadge, useToast, useConfirm } from '../components/ui.jsx'
import { PLATFORM_META, fmtDate } from '../lib'

const CHANNEL_EDITABLE = new Set(['queued', 'awaiting_approval', 'approved', 'error', 'tiktok_pending_approval'])

function Row({ job, accounts, selected, onToggleSelect, onChannelChange, onRetry, onRepublish, onDelete }) {
  const [showPlayer, setShowPlayer] = useState(false)
  // Jobs em produção já abrem com a linha do tempo visível — é exatamente quando
  // o operador quer acompanhar etapa a etapa, sem ter que clicar no ⏱.
  const [showTimeline, setShowTimeline] = useState(job.status === 'processing')
  const [showFullError, setShowFullError] = useState(false)
  const canEditChannel = CHANNEL_EDITABLE.has(job.status)
  const canRepublish   = job.status === 'approved' || job.status === 'error' || job.status === 'tiktok_pending_approval'

  const errMsg = job.error_message || ''
  const errFriendly = job.error_message_friendly || errMsg
  const errShort = errFriendly.length > 90 ? errFriendly.slice(0, 90) + '…' : errFriendly
  const hasTechnicalDetail = errMsg && errFriendly !== errMsg

  return (
    <div className="card px-3 py-2 fade-in">
      {/* Uma linha só no desktop; empilha no mobile (flex-wrap). */}
      <div className="flex items-center gap-2 sm:gap-3 flex-wrap">
        <input
          type="checkbox"
          className="shrink-0 accent-accent w-4 h-4"
          checked={selected}
          onChange={() => onToggleSelect(job.id)}
          aria-label={`Selecionar vídeo "${job.title}"`}
        />

        <div className="flex-1 min-w-0" style={{ flexBasis: '180px' }}>
          <p className="font-medium truncate text-sm leading-tight">
            {job.video_context?.is_trending && (
              <span className="badge text-[9px] mr-1.5 align-middle" style={{ background: 'rgba(255,182,39,0.16)', color: 'var(--warning)' }}>🔥 do momento</span>
            )}
            {job.video_context?.source === 'drive_ready_video' && (
              <span className="badge text-[9px] mr-1.5 align-middle" style={{ background: 'rgba(0,214,143,0.13)', color: 'var(--success)', border: '1px solid rgba(0,214,143,0.22)' }}>Drive</span>
            )}
            {job.title}
          </p>
          <div className="flex items-center gap-1.5 flex-wrap text-[11px] text-text-muted leading-tight">
            {(job.target_platforms || []).map((p) => {
              const m = PLATFORM_META[p]
              return m ? <span key={p} style={{ color: m.color }}>{m.icon}</span> : null
            })}
            <span>{job.content_type} · {fmtDate(job.created_at)}</span>
          </div>
        </div>

        <StatusBadge status={job.status} />
        {job.publish_status?.youtube?.stuck_processing && (
          <span
            className="badge text-[10px]"
            style={{ background: 'rgba(255,182,39,0.16)', color: 'var(--warning)', border: '1px solid rgba(255,182,39,0.3)' }}
            title="O YouTube aceitou o upload, mas o vídeo segue 'Pendente'/processando há mais de 24h — provavelmente nunca vai ficar público. Verifique no YouTube Studio; pode ser necessário excluir e reenviar.">
            ⚠ Travado no YouTube
          </span>
        )}

        <select
          className="input text-xs py-1.5 px-2 w-full sm:w-36 shrink-0 order-last sm:order-none"
          value={job.account_id ?? ''}
          disabled={!canEditChannel}
          title={canEditChannel ? 'Trocar canal' : 'Canal não editável neste status'}
          onChange={(e) => onChannelChange(job.id, e.target.value)}>
          <option value="">— sem canal —</option>
          {accounts.map((a) => (
            <option key={a.id} value={a.id}>{a.display_name}{a.platform ? ` (${a.platform})` : ''}</option>
          ))}
        </select>

        <div className="flex items-center gap-1 shrink-0">
          <button
            className="btn-ghost btn-sm"
            onClick={() => setShowTimeline((v) => !v)}
            title={showTimeline ? 'Ocultar etapas do pipeline' : 'Ver etapas do pipeline'}
            aria-expanded={showTimeline}
          >
            ⏱
          </button>
          {job.main_video_path && (
            <button className="btn-ghost btn-sm" onClick={() => setShowPlayer((v) => !v)} title={showPlayer ? 'Ocultar player' : 'Ver vídeo'}>
              {showPlayer ? 'Ocultar' : '▶ Ver'}
            </button>
          )}
          {canRepublish && (
            <button className="btn-ghost btn-sm" onClick={() => onRepublish(job.id)} title="Republicar">⤴</button>
          )}
          {(job.status === 'error' || job.status === 'tiktok_pending_approval') && (
            <button className="btn-ghost btn-sm" onClick={() => onRetry(job.id)} title="Tentar novamente">↻</button>
          )}
          <button className="btn-ghost btn-sm" onClick={() => onDelete(job.id)} title="Excluir">🗑</button>
        </div>
      </div>

      {/* Mensagem de erro expansível (largura total, abaixo da linha) */}
      {job.status === 'error' && errMsg && (
        <button
          className="text-left mt-1.5 w-full"
          onClick={() => setShowFullError((v) => !v)}
          title={showFullError ? 'Clique para recolher' : (hasTechnicalDetail ? 'Clique para ver o erro técnico' : 'Clique para ver completo')}>
          <p className="text-[11px] leading-snug" style={{ color: 'var(--error)' }}>
            {showFullError ? errFriendly : errShort}
            {(errFriendly.length > 90 || hasTechnicalDetail) && (
              <span className="opacity-70">{showFullError ? '  ▲' : '  ▼'}</span>
            )}
          </p>
          {showFullError && hasTechnicalDetail && (
            <p className="text-[10px] leading-snug mt-1 opacity-60 font-mono break-all">{errMsg}</p>
          )}
        </button>
      )}

      {/* Barra de progresso */}
      {job.status === 'processing' && (
        <div className="mt-2">
          <div className="h-1.5 rounded-full bg-elevated overflow-hidden">
            <div className="h-full rounded-full transition-all duration-500"
              style={{ width: `${job.progress || 0}%`, background: 'var(--grad-accent)' }} />
          </div>
          <p className="text-[10px] text-text-muted mt-0.5 truncate">{job.current_agent}</p>
        </div>
      )}

      {showTimeline && <PipelineTimeline job={job} />}

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
  const [total, setTotal]       = useState(0)
  const [pageSize, setPageSize] = useState(200)
  const [loaded, setLoaded]     = useState(false)
  const [loadError, setLoadError] = useState(false)
  const { accounts, error: accountsError, refresh: refreshAccounts } = useAccounts()
  const [selected, setSelected] = useState(() => new Set())
  const [modal, setModal]       = useState(false)
  const [statusFilter, setStatusFilter] = useState('all')
  const [sourceFilter, setSourceFilter] = useState('all')
  const [query, setQuery] = useState('')
  const fileRef = useRef(null)
  const { count } = useWs()
  const toast = useToast()
  const confirmDialog = useConfirm()

  // On failure, keep whatever jobs were last loaded (don't overwrite good data
  // with an empty list) but flag it so the UI can warn instead of showing a
  // plain "fila vazia" as if there simply were no jobs.
  // Filtros de status e texto vão SERVER-SIDE: antes o filtro só enxergava os
  // jobs já carregados na página (total > pageSize escondia resultados). O
  // debounce evita um GET a cada tecla digitada.
  const [debouncedQuery, setDebouncedQuery] = useState('')
  useEffect(() => {
    const t = setTimeout(() => setDebouncedQuery(query.trim()), 350)
    return () => clearTimeout(t)
  }, [query])

  const load = () => {
    const params = new URLSearchParams({ limit: String(pageSize) })
    if (statusFilter !== 'all') params.set('status', statusFilter)
    if (debouncedQuery) params.set('q', debouncedQuery)
    return api.get(`/jobs?${params}`)
      .then((d) => { setJobs(d.jobs || []); setTotal(d.total ?? (d.jobs || []).length); setLoadError(false) })
      .catch(() => setLoadError(true))
      .finally(() => setLoaded(true))
  }

  // Também recarrega quando pageSize cresce (botão "Carregar mais" abaixo) ou
  // quando os filtros server-side mudam.
  useEffect(() => { load() }, [pageSize, statusFilter, debouncedQuery])
  useEffect(() => { const t = setTimeout(load, 800); return () => clearTimeout(t) }, [count])
  const loadMore = () => setPageSize((n) => Math.min(n + 200, 500))
  const hasMore = jobs.length < total && pageSize < 500

  useEffect(() => {
    setSelected((prev) => {
      if (prev.size === 0) return prev
      const ids = new Set(jobs.map((j) => j.id))
      const next = new Set([...prev].filter((id) => ids.has(id)))
      return next.size === prev.size ? prev : next
    })
  }, [jobs])

  const retry = async (id) => {
    try { await api.post(`/jobs/${id}/retry`); load() }
    catch (err) { toast.error(err.message) }
  }

  const del = async (id) => {
    if (!await confirmDialog('Remover job?', { confirmLabel: 'Remover', danger: true })) return
    try { await api.del(`/jobs/${id}`); load() }
    catch (err) { toast.error(err.message) }
  }

  const importCsv = async (e) => {
    const f = e.target.files?.[0]
    if (!f) return
    try { const r = await api.upload('/jobs/import-csv', f); toast.success(`${r.count} jobs criados`); load() }
    catch (err) { toast.error(err.message) }
    e.target.value = ''
  }

  const changeChannel = async (id, val) => {
    try { await api.patch(`/jobs/${id}`, { account_id: val ? Number(val) : null }); load() }
    catch (err) { toast.error(err.message) }
  }

  const republish = async (id) => {
    try {
      const r = await api.post(`/jobs/${id}/publish`)
      const parts = []
      if (r?.note) parts.push(r.note)
      if (r?.pending_platforms?.length) parts.push(`Pendentes: ${r.pending_platforms.join(', ')}`)
      toast.success(parts.length ? parts.join('\n') : 'Republicação iniciada.')
      load()
    } catch (err) { toast.error(err.message) }
  }

  // Seleção múltipla
  const toggleSelect = (id) => {
    setSelected((prev) => {
      const next = new Set(prev)
      next.has(id) ? next.delete(id) : next.add(id)
      return next
    })
  }
  const errCount = jobs.filter((j) => j.status === 'error').length
  // Status e texto já vieram filtrados do servidor; só a fonte (Drive/IA)
  // continua client-side — é um recorte visual raro, não vale round-trip.
  const filteredJobs = jobs.filter((j) => {
    const source = j.video_context?.source === 'drive_ready_video' ? 'drive' : 'ai'
    return sourceFilter === 'all' || sourceFilter === source
  })

  // "Select all" must operate on filteredJobs (what the user actually sees), not the
  // full jobs array, otherwise a filtered view can silently select/delete hidden jobs.
  const allSelected = filteredJobs.length > 0 && selected.size === filteredJobs.length && filteredJobs.every((j) => selected.has(j.id))
  const toggleSelectAll = () => {
    setSelected(allSelected ? new Set() : new Set(filteredJobs.map((j) => j.id)))
  }

  const deleteSelected = async () => {
    const ids = [...selected]
    if (ids.length === 0) return
    if (!await confirmDialog(`Excluir ${ids.length} job(s) selecionado(s)?`, { confirmLabel: 'Excluir', danger: true })) return
    try { await api.post('/jobs/bulk-delete', { ids }); setSelected(new Set()); load() }
    catch (err) { toast.error(err.message) }
  }

  const clearErrors = async () => {
    if (!await confirmDialog('Excluir todos os jobs com erro?', { confirmLabel: 'Excluir', danger: true })) return
    try { await api.post('/jobs/bulk-delete', { status: 'error' }); setSelected(new Set()); load() }
    catch (err) { toast.error(err.message) }
  }

  return (
    <div className="space-y-4 fade-in">
      <PageHeader title={
        <span className="inline-flex items-center gap-2.5">
          Fila
          <span className="badge" style={{ background: 'var(--accent-dim)', color: 'var(--accent)', border: '1px solid var(--border)' }}>{total || jobs.length}</span>
          {errCount > 0 && (
            <span className="badge" style={{ background: 'rgba(255,80,102,0.12)', color: 'var(--error)', border: '1px solid rgba(255,80,102,0.2)' }}>
              {errCount} erro{errCount > 1 ? 's' : ''}
            </span>
          )}
        </span>
      }>
        <button className="btn btn-ghost text-sm" onClick={() => fileRef.current?.click()}>⬆ Importar CSV</button>
        <input ref={fileRef} type="file" accept=".csv" className="hidden" onChange={importCsv} />
        <button className="btn btn-primary text-sm" onClick={() => setModal(true)}>+ Novo vídeo</button>
      </PageHeader>

      <p className="text-xs text-text-muted">
        CSV: <code className="font-mono bg-elevated px-1.5 py-0.5 rounded">title, topic, content_type, mode, target_platforms (a|b), account_id</code>
      </p>

      {(loadError || accountsError) && (
        <ErrorBanner message="Backend offline — não foi possível atualizar a fila." onRetry={() => { load(); refreshAccounts() }} />
      )}

      {jobs.length > 0 && (
        <div className="card p-3 flex items-center gap-3 flex-wrap">
          <input
            className="input text-sm sm:max-w-[260px]"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Buscar por titulo ou tipo"
          />
          <select className="input text-sm sm:w-44" value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)}>
            <option value="all">Todos status</option>
            <option value="queued">Na fila</option>
            <option value="processing">Processando</option>
            <option value="awaiting_approval">Aguardando aprovacao</option>
            <option value="approved">Aprovados</option>
            <option value="published">Publicados</option>
            <option value="error">Com erro</option>
          </select>
          <select className="input text-sm sm:w-36" value={sourceFilter} onChange={(e) => setSourceFilter(e.target.value)}>
            <option value="all">Toda fonte</option>
            <option value="drive">Drive</option>
            <option value="ai">IA</option>
          </select>
          <div className="flex-1" />
          <label className="flex items-center gap-2 text-xs text-text-muted cursor-pointer select-none">
            <input type="checkbox" className="accent-accent" checked={allSelected} onChange={toggleSelectAll} />
            Selecionar todos
          </label>
          {selected.size > 0 && (
            <span className="text-xs text-text-muted">{selected.size} selecionado(s)</span>
          )}
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

      {hasMore && (
        <div className="flex items-center gap-2 flex-wrap text-xs text-text-muted">
          <span>Mostrando {jobs.length} de {total} jobs{debouncedQuery || statusFilter !== 'all' ? ' (filtro aplicado no servidor)' : ' mais recentes'}.</span>
          <button className="btn-ghost text-xs" onClick={loadMore}>Carregar mais</button>
        </div>
      )}

      {!loaded ? (
        <div className="space-y-1.5">
          {Array.from({ length: 5 }).map((_, i) => <div key={i} className="skeleton h-14 rounded-card" />)}
        </div>
      ) : jobs.length === 0 ? (
        <div className="rounded-card" style={{ background: 'var(--bg-surface)', border: '1px solid var(--border)' }}>
          {loadError
            ? <EmptyState icon="⚠" title="Não foi possível carregar a fila."
                hint="Verifique se o backend está no ar e tente novamente."
                action={<button className="btn btn-primary text-sm" onClick={load}>↻ Tentar novamente</button>} />
            : <EmptyState icon="🎬" title="Fila vazia."
                hint="Crie um vídeo ou importe um CSV para começar."
                action={<button className="btn btn-primary text-sm" onClick={() => setModal(true)}>+ Novo vídeo</button>} />}
        </div>
      ) : filteredJobs.length === 0 ? (
        <div className="rounded-card" style={{ background: 'var(--bg-surface)', border: '1px solid var(--border)' }}>
          <EmptyState icon="?" title="Nada encontrado."
            hint="Ajuste os filtros para ver outros jobs da fila." />
        </div>
      ) : (
        <div className="space-y-1.5">
          {filteredJobs.map((j) => (
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
      )}

      <AddJobModal open={modal} onClose={() => setModal(false)} onCreated={load} />
    </div>
  )
}
