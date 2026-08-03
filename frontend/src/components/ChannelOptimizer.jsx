import { useEffect, useState } from 'react'
import { api } from '../api'
import { Modal } from './ui.jsx'

const ACTION_META = {
  auto: { label: 'será aplicado', color: 'var(--success)', bg: 'rgba(0,245,160,0.14)' },
  confirm: { label: 'confirmar', color: 'var(--warning)', bg: 'rgba(255,182,39,0.16)' },
  nochange: { label: 'já ok', color: 'var(--text-muted)', bg: 'rgba(255,255,255,0.06)' },
}

function friendlyError(message) {
  const text = String(message || '')
  if (/quota|exceeded|403/i.test(text)) {
    return 'Quota diaria do YouTube atingida. O Google bloqueou a leitura/otimizacao do canal agora. Aguarde o reset da quota ou solicite aumento no Google Cloud.'
  }
  return text
}

export default function ChannelOptimizer({ account, onClose, onApplied }) {
  const [plan, setPlan] = useState(null)
  const [err, setErr] = useState(null)
  const [confirmed, setConfirmed] = useState({})   // {fieldKey: true}
  const [applying, setApplying] = useState(false)
  const [result, setResult] = useState(null)
  const [doneItems, setDoneItems] = useState(new Set())
  const [copied, setCopied] = useState(null)

  const analyze = async () => {
    setErr(null); setPlan(null); setResult(null)
    try {
      const p = await api.post(`/accounts/${account.id}/optimize/analyze`)
      setPlan(p)
    } catch (e) { setErr(friendlyError(e.message)) }
  }
  useEffect(() => {
    analyze()
    api.get(`/accounts/${account.id}/optimize`).then((s) => {
      setDoneItems(new Set(s?.checklist_done || []))
    }).catch(() => {})
  }, [account.id])

  const apply = async () => {
    setApplying(true); setErr(null)
    try {
      const confirmedFields = (plan.fields || [])
        .filter((f) => f.action === 'confirm' && confirmed[f.key]).map((f) => f.key)
      // Send the exact proposal the user reviewed, so the server doesn't re-run the LLM
      // (that second call was what made apply slow enough to look frozen).
      const proposed = {}
      for (const f of (plan.fields || [])) proposed[f.key] = f.proposed
      const res = await api.post(`/accounts/${account.id}/optimize/apply`,
        { confirmed_fields: confirmedFields, proposed }, { timeoutMs: 90000 })
      setResult(res)
      onApplied?.()
    } catch (e) { setErr(friendlyError(e.message)) }
    finally { setApplying(false) }
  }

  const toggleDone = async (id, done) => {
    const next = new Set(doneItems)
    done ? next.add(id) : next.delete(id)
    setDoneItems(next)
    try { await api.post(`/accounts/${account.id}/optimize/checklist`, { item_id: id, done }) } catch { /* */ }
  }

  const copy = (text, id) => {
    navigator.clipboard?.writeText(text)
    setCopied(id); setTimeout(() => setCopied(null), 1500)
  }

  const fields = plan?.fields || []
  const willApply = fields.some((f) => f.action === 'auto') ||
    fields.some((f) => f.action === 'confirm' && confirmed[f.key])

  return (
    <Modal onClose={onClose} labelledBy="channel-optimizer-modal-title"
      overlayClassName="fixed inset-0 z-50 flex items-center justify-center p-4"
      overlayStyle={{ background: 'rgba(0,0,0,0.65)', backdropFilter: 'blur(3px)' }}
      cardClassName="card w-full max-w-2xl max-h-[88vh] overflow-y-auto p-0 fade-in">

        {/* Header */}
        <div className="flex items-center justify-between px-5 py-4 border-b sticky top-0 z-10"
          style={{ borderColor: 'rgba(255,255,255,0.07)', background: 'var(--bg-surface)' }}>
          <div>
            <h3 id="channel-optimizer-modal-title" className="heading font-semibold">✨ Otimizar canal</h3>
            <p className="text-[11px] text-text-muted">{plan?.title || account.display_name}</p>
          </div>
          <button className="btn-ghost text-sm px-2" onClick={onClose} aria-label="Fechar">✕</button>
        </div>

        <div className="p-5 space-y-5">
          {err && (
            <div className="rounded-card p-3 text-xs" style={{ background: 'rgba(255,80,102,0.1)', color: 'var(--error)' }}>
              {err}
            </div>
          )}

          {!plan && !err && (
            <div className="space-y-2">
              {[1, 2, 3].map((i) => <div key={i} className="skeleton h-10 rounded-card" />)}
              <p className="text-xs text-text-muted text-center pt-2">Analisando o canal…</p>
            </div>
          )}

          {plan && (
            <>
              {/* Auto-apply / confirm fields */}
              <section>
                <h4 className="text-xs font-semibold text-text-muted uppercase tracking-wide mb-2">
                  Identidade do canal (via API)
                </h4>
                <div className="space-y-2">
                  {fields.map((f) => {
                    const meta = ACTION_META[f.action]
                    return (
                      <div key={f.key} className="rounded-card p-3" style={{ background: 'var(--bg-elevated)' }}>
                        <div className="flex items-center justify-between gap-2 mb-1">
                          <span className="text-sm font-medium">{f.label}</span>
                          <div className="flex items-center gap-2">
                            <span className="badge text-[10px]" style={{ background: meta.bg, color: meta.color }}>{meta.label}</span>
                            {f.action === 'confirm' && (
                              <input type="checkbox" checked={!!confirmed[f.key]}
                                onChange={(e) => setConfirmed((c) => ({ ...c, [f.key]: e.target.checked }))} />
                            )}
                          </div>
                        </div>
                        {f.action !== 'nochange' && (
                          <div className="text-[11px] space-y-0.5">
                            {f.current && <p className="text-text-muted line-through truncate">{f.current}</p>}
                            <p style={{ color: 'var(--text-primary)' }} className="break-words">{f.proposed}</p>
                          </div>
                        )}
                        {f.action === 'confirm' && (
                          <p className="text-[10px] mt-1" style={{ color: 'var(--warning)' }}>
                            Já tem valor — marque para substituir.
                          </p>
                        )}
                      </div>
                    )
                  })}
                </div>
              </section>

              {/* Playlists + category */}
              {(plan.playlists?.length > 0) && (
                <section>
                  <h4 className="text-xs font-semibold text-text-muted uppercase tracking-wide mb-2">
                    Playlists de nicho (sessão + SEO)
                  </h4>
                  <div className="flex flex-wrap gap-1.5">
                    {plan.playlists.map((p, i) => (
                      <span key={i} className="badge text-[11px]" style={{ background: 'var(--accent-dim)', color: 'var(--accent)' }}>{p}</span>
                    ))}
                  </div>
                </section>
              )}

              {/* Apply */}
              <button className="btn btn-primary w-full" disabled={applying || (!willApply)} onClick={apply}>
                {applying ? 'Aplicando…' : willApply ? 'Aplicar otimização' : 'Nada a aplicar (já otimizado)'}
              </button>
              {result?.ok && (
                <p className="text-xs text-center" style={{ color: 'var(--success)' }}>
                  ✓ Aplicado: {Object.keys(result.applied || {}).join(', ') || 'nada novo'}
                  {result.results?.playlists?.length ? ` · ${result.results.playlists.length} playlist(s)` : ''}
                </p>
              )}

              {/* Studio checklist */}
              {plan.checklist?.length > 0 && (
                <section>
                  <h4 className="text-xs font-semibold text-text-muted uppercase tracking-wide mb-2">
                    Checklist do Studio (1 clique — a API não faz)
                  </h4>
                  <div className="space-y-2">
                    {plan.checklist.map((c) => (
                      <div key={c.id} className="rounded-card p-3" style={{ background: 'var(--bg-elevated)' }}>
                        <div className="flex items-start gap-2">
                          <input type="checkbox" className="mt-0.5" checked={doneItems.has(c.id)}
                            onChange={(e) => toggleDone(c.id, e.target.checked)} />
                          <div className="flex-1 min-w-0">
                            <p className="text-sm font-medium">{c.label}</p>
                            <p className="text-[11px] text-text-muted">{c.why}</p>
                            <div className="flex items-center gap-2 mt-1.5">
                              <a href={c.studio_url} target="_blank" rel="noreferrer"
                                className="text-[11px] font-medium" style={{ color: 'var(--accent)' }}>Abrir no Studio ↗</a>
                              {c.copy_text && (
                                <button className="text-[11px] font-medium" style={{ color: 'var(--accent)' }}
                                  onClick={() => copy(c.copy_text, c.id)}>
                                  {copied === c.id ? '✓ copiado' : 'Copiar lista'}
                                </button>
                              )}
                            </div>
                          </div>
                        </div>
                      </div>
                    ))}
                  </div>
                </section>
              )}

              <p className="text-[10px] text-text-muted leading-relaxed border-t pt-3" style={{ borderColor: 'rgba(255,255,255,0.06)' }}>
                A IA configura o que a API grátis do YouTube permite (palavras-chave, descrição, país, idioma, playlists).
                Nome, banner, trailer e moderação são só no Studio — por isso viram checklist. Nada que você definiu de
                propósito é sobrescrito sem o seu "confirmar".
              </p>
            </>
          )}
        </div>
    </Modal>
  )
}
