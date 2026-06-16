import { useState } from 'react'
import { api, mediaUrl } from '../api'
import { PLATFORM_META, fmtDate } from '../lib'

export default function ApprovalCard({ job, onDone }) {
  const [showPlayer, setShowPlayer] = useState(false)
  const [editing, setEditing] = useState(false)
  const [seo, setSeo] = useState(job.seo_metadata || {})
  const [busy, setBusy] = useState(false)
  const ctx = job.video_context || {}
  const qc = ctx.qc || {}
  const compliance = ctx.compliance || {}

  const act = async (kind) => {
    setBusy(true)
    try {
      if (editing) await api.patch(`/jobs/${job.id}/seo`, { seo_metadata: seo })
      const res = await api.post(`/jobs/${job.id}/${kind}`)
      // Ao aprovar, o backend pode devolver uma 'note' (ex.: publisher não
      // configurado). Mostra ao usuário para deixar claro que NÃO publicou.
      if (kind === 'approve' && res && res.note) alert(res.note)
      onDone?.()
    } catch (e) { alert(e.message) } finally { setBusy(false) }
  }
  const saveSeo = async () => {
    try {
      await api.patch(`/jobs/${job.id}/seo`, { seo_metadata: seo })
      setEditing(false)
    } catch (e) { alert(e.message) }
  }
  const remove = async () => {
    if (!confirm('Excluir este job e seus arquivos? Esta ação não pode ser desfeita.')) return
    setBusy(true)
    try {
      await api.del(`/jobs/${job.id}`)
      onDone?.()
    } catch (e) { alert(e.message) } finally { setBusy(false) }
  }

  const yt = seo.youtube || {}

  return (
    <div className="relative">
      <div className="absolute left-0 top-4 bottom-4 w-0.5 rounded-full" style={{ background: 'var(--grad-accent)' }} />
      <div className="card card-hover p-4 pl-5 fade-in">
        <div className="flex gap-4">
          <div className="w-64 shrink-0">
            {showPlayer ? (
              <video src={mediaUrl(job.main_video_path)} controls className="w-full rounded-btn bg-black aspect-video" />
            ) : (
              <button onClick={() => setShowPlayer(true)} className="relative w-full aspect-video rounded-btn overflow-hidden bg-elevated group">
                {job.thumbnail_path
                  ? <img src={mediaUrl(job.thumbnail_path)} className="w-full h-full object-cover" alt="" />
                  : <span className="absolute inset-0 grid place-items-center text-3xl opacity-40">🎬</span>}
                <span className="absolute inset-0 grid place-items-center bg-black/20 group-hover:bg-black/45 transition-all duration-200">
                  <div className="w-12 h-12 rounded-full bg-white/10 border border-white/20 flex items-center justify-center text-xl">▶</div>
                </span>
              </button>
            )}
            <div className="flex flex-wrap gap-1 mt-2">
              {(job.target_platforms || []).map((p) => {
                const m = PLATFORM_META[p]
                return m ? <span key={p} className="badge text-[10px]" style={{ background: m.color + '22', color: m.color }}>{m.icon} {m.label}</span> : null
              })}
            </div>
            <p className="text-[11px] text-text-muted mt-1">
              {ctx.narration_duration ? `${Math.round(ctx.narration_duration)}s · ` : ''}
              {(job.shorts_paths || []).length} shorts · agendado {fmtDate(job.scheduled_at)}
            </p>
          </div>

          <div className="flex-1 min-w-0 pl-4">
            <div className="flex items-start justify-between gap-2">
              <h3 className="font-medium">{job.title}{job.is_mirror && <span className="badge bg-accent/20 text-accent ml-2 text-[10px]">mirror</span>}</h3>
            </div>

            <p className="text-[10px] uppercase tracking-wider text-text-muted mt-2 mb-1">Validação</p>
            <div className="flex gap-2 mb-3">
              <span className="badge text-[10px]" style={{ background: qc.status === 'qc_passed' ? 'rgba(0,214,143,.15)' : 'rgba(255,182,39,.15)', color: qc.status === 'qc_passed' ? 'var(--success)' : 'var(--warning)' }}>QC: {qc.status || job.qc_status || '—'}</span>
              <span className="badge text-[10px]" style={{ background: compliance.status === 'approved' ? 'rgba(0,214,143,.15)' : 'rgba(255,71,87,.15)', color: compliance.status === 'approved' ? 'var(--success)' : 'var(--error)' }}>Compliance: {compliance.status || job.compliance_status || '—'}</span>
            </div>

            {(qc.warnings?.length > 0 || compliance.blocks?.length > 0) && (
              <ul className="text-[11px] text-warning mb-3 list-disc list-inside space-y-0.5">
                {(compliance.blocks || []).map((b, i) => <li key={'b' + i} className="text-error">{b}</li>)}
                {(qc.warnings || []).map((w, i) => <li key={'w' + i}>{w}</li>)}
              </ul>
            )}

            {editing ? (
              <div className="space-y-2">
                <input className="input" value={yt.title || ''} onChange={(e) => setSeo({ ...seo, youtube: { ...yt, title: e.target.value } })} placeholder="Título YouTube" />
                <textarea className="input h-20 resize-none" value={yt.description || ''} onChange={(e) => setSeo({ ...seo, youtube: { ...yt, description: e.target.value } })} placeholder="Descrição" />
                <input className="input" value={(yt.tags || []).join(', ')} onChange={(e) => setSeo({ ...seo, youtube: { ...yt, tags: e.target.value.split(',').map((t) => t.trim()).filter(Boolean) } })} placeholder="Tags (vírgula)" />
                <div className="flex gap-2"><button className="btn-primary text-xs" onClick={saveSeo}>Salvar SEO</button><button className="btn-ghost text-xs" onClick={() => setEditing(false)}>Cancelar</button></div>
              </div>
            ) : (
              <div className="text-sm text-text-muted space-y-1">
                <p><span className="text-text-primary font-medium">{yt.title || job.title}</span></p>
                <p className="line-clamp-2 text-[12px]">{yt.description}</p>
              </div>
            )}

            <div className="flex gap-2 mt-4">
              <button disabled={busy} className="btn-success text-xs px-6" onClick={() => act('approve')}>✓ Aprovar</button>
              <button disabled={busy} className="btn-danger text-xs px-6" onClick={() => act('reject')}>✗ Rejeitar</button>
              {!editing && <button className="btn-ghost text-xs" onClick={() => setEditing(true)}>✏ Editar SEO</button>}
              <button disabled={busy} className="btn-ghost text-xs text-error" onClick={remove}>🗑 Excluir</button>
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}
