import { useEffect, useState } from 'react'
import { api, mediaUrl } from '../api'
import { PLATFORM_META, fmtDate } from '../lib'
import { useToast, useConfirm } from './ui.jsx'

// Cor semafórica compartilhada pelos dois scores.
function scoreColor(v) {
  if (v == null) return 'var(--text-dim)'
  if (v >= 85) return 'var(--success)'
  if (v >= 60) return 'var(--warning)'
  return 'var(--error)'
}

// Barra de score 0–100. `null` (job antigo, anterior ao score) mostra "—" em
// vez de inventar um número.
function ScoreBar({ label, value }) {
  const color = scoreColor(value)
  return (
    <div className="min-w-[130px] flex-1">
      <div className="flex items-center justify-between text-[10px] font-semibold uppercase" style={{ color: 'var(--text-muted)', letterSpacing: '.06em' }}>
        <span>{label}</span>
        <span className="font-mono" style={{ color }}>{value == null ? '—' : value}</span>
      </div>
      <div className="h-1.5 rounded-full mt-1 overflow-hidden" style={{ background: 'var(--bg-elevated)' }}>
        {value != null && (
          <div className="h-full rounded-full transition-all duration-700" style={{ width: `${value}%`, background: color }} />
        )}
      </div>
    </div>
  )
}

export default function ApprovalCard({ job, onDone }) {
  const [showPlayer, setShowPlayer] = useState(false)
  const [editing, setEditing] = useState(false)
  const [seo, setSeo] = useState(job.seo_metadata || {})

  // Approvals.jsx keys cards by job.id, so this instance persists across
  // list refreshes (e.g. websocket-triggered reloads). Re-sync local seo
  // state whenever the server-provided job.seo_metadata changes, unless the
  // user is actively editing it, to avoid clobbering newer server data.
  useEffect(() => {
    if (!editing) setSeo(job.seo_metadata || {})
  }, [job.seo_metadata, editing])
  const [busy, setBusy] = useState(false)
  const ctx = job.video_context || {}
  const qc = ctx.qc || {}
  const compliance = ctx.compliance || {}
  const toast = useToast()
  const confirmDialog = useConfirm()

  const act = async (kind) => {
    setBusy(true)
    try {
      if (editing) await api.patch(`/jobs/${job.id}/seo`, { seo_metadata: seo })
      const res = await api.post(`/jobs/${job.id}/${kind}`)
      // Ao aprovar, o backend pode devolver uma 'note' (ex.: publisher não
      // configurado). Mostra ao usuário para deixar claro que NÃO publicou.
      if (kind === 'approve' && res && res.note) toast.info(res.note)
      onDone?.()
    } catch (e) { toast.error(e.message) } finally { setBusy(false) }
  }
  const regenerate = async () => {
    if (!await confirmDialog('Regenerar este vídeo do zero? O pipeline inteiro será reexecutado.', { confirmLabel: 'Regenerar' })) return
    setBusy(true)
    try { await api.post(`/jobs/${job.id}/retry`); toast.info('Regeneração iniciada — acompanhe na Fila.'); onDone?.() }
    catch (e) { toast.error(e.message) } finally { setBusy(false) }
  }
  const saveSeo = async () => {
    try {
      await api.patch(`/jobs/${job.id}/seo`, { seo_metadata: seo })
      setEditing(false)
    } catch (e) { toast.error(e.message) }
  }
  const remove = async () => {
    if (!await confirmDialog('Excluir este job e seus arquivos? Esta ação não pode ser desfeita.', { confirmLabel: 'Excluir', danger: true })) return
    setBusy(true)
    try {
      await api.del(`/jobs/${job.id}`)
      onDone?.()
    } catch (e) { toast.error(e.message) } finally { setBusy(false) }
  }

  const yt = seo.youtube || {}
  const tk = seo.tiktok || {}
  const ig = seo.instagram || {}
  // cross_platform_linker.py forces every TikTok/Instagram mirror into
  // AWAITING_APPROVAL specifically so a human reviews it before it goes out —
  // but this card used to only ever show/edit the YouTube block, so the
  // reviewer approved mirrors blind to the actual caption/hashtags that would
  // be published on the OTHER platform. Also preview these in the real 9:16
  // aspect instead of a 16:9 box that misrepresents the vertical crop.
  const targets = job.target_platforms || []
  const isOtherPlatformMirror = !!job.is_mirror || targets.includes('tiktok') || targets.includes('instagram')
  const previewAspect = isOtherPlatformMirror ? 'aspect-[9/16] max-w-[220px] mx-auto' : 'aspect-video'

  return (
    <div className="relative">
      <div className="absolute left-0 top-4 bottom-4 w-0.5 rounded-full" style={{ background: 'var(--grad-accent)' }} />
      <div className="card card-hover p-4 pl-5 fade-in">
        <div className="flex flex-col sm:flex-row gap-4">
          <div className="w-full sm:w-64 shrink-0">
            {showPlayer ? (
              <video src={mediaUrl(job.main_video_path)} controls className={`w-full rounded-btn bg-black ${previewAspect}`} />
            ) : (
              <button onClick={() => setShowPlayer(true)} className={`relative w-full rounded-btn overflow-hidden bg-elevated group ${previewAspect}`}>
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

          <div className="flex-1 min-w-0 sm:pl-4">
            <div className="flex items-start justify-between gap-2">
              <h3 className="font-medium">{job.title}{job.is_mirror && <span className="badge bg-accent/20 text-accent ml-2 text-[10px]">mirror</span>}</h3>
            </div>

            <p className="text-[10px] uppercase tracking-wider text-text-muted mt-2 mb-1">Validação</p>

            {/* Scores reais do pipeline: qualidade (QC técnico) e risco (compliance) */}
            <div className="flex gap-4 mb-2">
              <ScoreBar label="Qualidade" value={qc.score ?? (qc.status === 'qc_passed' ? 100 : null)} />
              <ScoreBar label="Risco" value={compliance.risk_score ?? (compliance.status === 'approved' && !(compliance.suggestions || []).length ? 100 : null)} />
            </div>

            <div className="flex gap-2 mb-3 flex-wrap">
              <span className="badge text-[10px]" style={{ background: qc.status === 'qc_passed' ? 'rgba(0,214,143,.15)' : 'rgba(255,182,39,.15)', color: qc.status === 'qc_passed' ? 'var(--success)' : 'var(--warning)' }}>QC: {qc.status || job.qc_status || '—'}</span>
              <span className="badge text-[10px]" style={{ background: compliance.status === 'approved' ? 'rgba(0,214,143,.15)' : 'rgba(255,71,87,.15)', color: compliance.status === 'approved' ? 'var(--success)' : 'var(--error)' }}>Compliance: {compliance.status || job.compliance_status || '—'}</span>
              {/* Metadados técnicos reais medidos pelo ffprobe */}
              {qc.meta?.width > 0 && (
                <span className="badge text-[10px]" style={{ background: 'var(--bg-elevated)', color: 'var(--text-muted)', border: '1px solid var(--border)' }}>
                  {qc.meta.width}×{qc.meta.height}
                </span>
              )}
              {qc.meta?.duration > 0 && (
                <span className="badge text-[10px]" style={{ background: 'var(--bg-elevated)', color: 'var(--text-muted)', border: '1px solid var(--border)' }}>
                  {Math.round(qc.meta.duration)}s
                </span>
              )}
              {qc.meta?.bitrate > 0 && (
                <span className="badge text-[10px]" style={{ background: 'var(--bg-elevated)', color: 'var(--text-muted)', border: '1px solid var(--border)' }}>
                  {(qc.meta.bitrate / 1e6).toFixed(1)} Mbps
                </span>
              )}
              {qc.meta && qc.meta.has_audio === false && (
                <span className="badge text-[10px]" style={{ background: 'rgba(255,71,87,.12)', color: 'var(--error)', border: '1px solid rgba(255,71,87,.25)' }}>
                  sem áudio
                </span>
              )}
            </div>

            {(qc.warnings?.length > 0 || compliance.blocks?.length > 0 || compliance.suggestions?.length > 0) && (
              <ul className="text-[11px] text-warning mb-3 list-disc list-inside space-y-0.5">
                {(compliance.blocks || []).map((b, i) => <li key={'b' + i} className="text-error">{b}</li>)}
                {(qc.warnings || []).map((w, i) => <li key={'w' + i}>{w}</li>)}
                {(compliance.suggestions || []).map((s, i) => <li key={'s' + i} style={{ color: 'var(--text-muted)' }}>{s}</li>)}
              </ul>
            )}

            {editing ? (
              <div className="space-y-2">
                <input className="input" value={yt.title || ''} onChange={(e) => setSeo({ ...seo, youtube: { ...yt, title: e.target.value } })} placeholder="Título YouTube" />
                <textarea className="input h-20 resize-none" value={yt.description || ''} onChange={(e) => setSeo({ ...seo, youtube: { ...yt, description: e.target.value } })} placeholder="Descrição" />
                <input className="input" value={(yt.tags || []).join(', ')} onChange={(e) => setSeo({ ...seo, youtube: { ...yt, tags: e.target.value.split(',').map((t) => t.trim()).filter(Boolean) } })} placeholder="Tags (vírgula)" />
                {targets.includes('tiktok') && (
                  <textarea className="input h-16 resize-none" value={tk.caption || ''} onChange={(e) => setSeo({ ...seo, tiktok: { ...tk, caption: e.target.value } })} placeholder="Legenda TikTok" />
                )}
                {targets.includes('instagram') && (
                  <>
                    <textarea className="input h-16 resize-none" value={ig.caption || ''} onChange={(e) => setSeo({ ...seo, instagram: { ...ig, caption: e.target.value } })} placeholder="Legenda Instagram" />
                    <input className="input" value={(ig.hashtags || []).join(', ')} onChange={(e) => setSeo({ ...seo, instagram: { ...ig, hashtags: e.target.value.split(',').map((t) => t.trim()).filter(Boolean) } })} placeholder="Hashtags Instagram (vírgula)" />
                  </>
                )}
                <div className="flex gap-2"><button className="btn-primary text-xs" onClick={saveSeo}>Salvar SEO</button><button className="btn-ghost text-xs" onClick={() => setEditing(false)}>Cancelar</button></div>
              </div>
            ) : (
              <div className="text-sm text-text-muted space-y-1">
                <p><span className="text-text-primary font-medium">{yt.title || job.title}</span></p>
                <p className="line-clamp-2 text-[12px]">{yt.description}</p>
                {targets.includes('tiktok') && tk.caption && (
                  <p className="line-clamp-2 text-[12px]"><span className="text-text-primary">TikTok:</span> {tk.caption}</p>
                )}
                {targets.includes('instagram') && (ig.caption || ig.hashtags?.length > 0) && (
                  <p className="line-clamp-2 text-[12px]">
                    <span className="text-text-primary">Instagram:</span> {ig.caption} {(ig.hashtags || []).map((h) => `#${h}`).join(' ')}
                  </p>
                )}
                {(yt.tags || []).length > 0 && (
                  <div className="flex flex-wrap items-center gap-1 pt-1">
                    {yt.tags.slice(0, 5).map((t) => (
                      <span key={t} className="badge text-[9px] normal-case font-medium" style={{ background: 'var(--bg-elevated)', color: 'var(--text-muted)' }}>{t}</span>
                    ))}
                    {yt.tags.length > 5 && (
                      <span className="text-[10px] text-text-muted">+{yt.tags.length - 5}</span>
                    )}
                  </div>
                )}
              </div>
            )}

            <div className="flex flex-wrap gap-2 mt-4">
              <button disabled={busy} className="btn-success text-xs px-4 sm:px-6 min-h-[36px] flex-1 sm:flex-none" onClick={() => act('approve')}>✓ Aprovar</button>
              <button disabled={busy} className="btn-danger text-xs px-4 sm:px-6 min-h-[36px] flex-1 sm:flex-none" onClick={() => act('reject')}>✗ Rejeitar</button>
              {!editing && <button className="btn-ghost text-xs min-h-[36px]" onClick={() => setEditing(true)}>✏ Editar SEO</button>}
              <button disabled={busy} className="btn-ghost text-xs min-h-[36px]" onClick={regenerate} title="Reexecuta o pipeline inteiro (roteiro, narração, edição…)">↻ Regenerar</button>
              <button disabled={busy} className="btn-ghost text-xs text-error min-h-[36px]" onClick={remove}>🗑 Excluir</button>
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}
