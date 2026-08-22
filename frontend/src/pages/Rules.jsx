import { useCallback, useEffect, useState } from 'react'
import { api } from '../api'
import { PageHeader, SectionCard, EmptyState, ErrorBanner, useToast, useConfirm, Modal } from '../components/ui.jsx'
import { fmtDate } from '../lib'

// Página Automações (§27) — CRUD real das regras SE->ENTÃO executadas pela
// engine (backend/agents/automation_engine.py). Cada regra mostra quantas
// vezes já rodou de verdade (runs) e quando.

const TRIGGER_ICON = {
  job_awaiting_approval: '⏸',
  job_approved: '✓',
  job_error: '✕',
  job_published: '🚀',
}
const ACTION_ICON = {
  notify: '🔔',
  retry_job: '↻',
  collect_analytics: '📊',
}

function RuleCard({ rule, onToggle, onDelete }) {
  return (
    <div className="rounded-card p-4 flex items-center gap-3 flex-wrap fade-in"
      style={{ background: 'var(--bg-surface)', border: '1px solid var(--border)', opacity: rule.enabled ? 1 : 0.6 }}>
      <span className="w-9 h-9 rounded-btn grid place-items-center text-base shrink-0"
        style={{ background: 'var(--bg-elevated)', border: '1px solid var(--border)' }}>
        {TRIGGER_ICON[rule.trigger] || '⚙'}
      </span>
      <div className="min-w-0 flex-1">
        <p className="text-sm font-bold truncate" style={{ color: 'var(--text-primary)' }}>{rule.name}</p>
        <p className="text-[11px]" style={{ color: 'var(--text-muted)' }}>
          {TRIGGER_ICON[rule.trigger]} → {ACTION_ICON[rule.action]} {rule.action}
          {rule.config?.max_attempts ? ` · máx. ${rule.config.max_attempts} tentativas` : ''}
        </p>
        <p className="text-[10px] mt-0.5 font-mono" style={{ color: 'var(--text-dim)' }}>
          {rule.runs > 0
            ? `executada ${rule.runs}× · última ${fmtDate(rule.last_run_at)}`
            : 'ainda não executada'}
        </p>
      </div>
      <button
        className="badge text-[10px] cursor-pointer"
        onClick={() => onToggle(rule)}
        title={rule.enabled ? 'Desativar regra' : 'Ativar regra'}
        style={rule.enabled
          ? { background: 'color-mix(in srgb, var(--success) 14%, transparent)', color: 'var(--success)', border: '1px solid color-mix(in srgb, var(--success) 30%, transparent)' }
          : { background: 'var(--bg-elevated)', color: 'var(--text-dim)', border: '1px solid var(--border)' }}
      >
        {rule.enabled ? 'ATIVA' : 'INATIVA'}
      </button>
      <button className="btn-ghost btn-sm text-error" onClick={() => onDelete(rule)} title="Excluir regra">🗑</button>
    </div>
  )
}

export default function Rules() {
  const [data, setData] = useState(null)
  const [loadError, setLoadError] = useState(false)
  const [modal, setModal] = useState(false)
  const [name, setName] = useState('')
  const [trigger, setTrigger] = useState('job_awaiting_approval')
  const [action, setAction] = useState('notify')
  const [maxAttempts, setMaxAttempts] = useState(2)
  const [saving, setSaving] = useState(false)
  const toast = useToast()
  const confirmDialog = useConfirm()

  const load = useCallback(() => api.get('/automation/rules')
    .then((d) => { setData(d); setLoadError(false) })
    .catch(() => setLoadError(true)), [])

  useEffect(() => { load() }, [load])

  const toggle = async (rule) => {
    try { await api.patch(`/automation/rules/${rule.id}`, { enabled: !rule.enabled }); load() }
    catch (e) { toast.error(e.message) }
  }

  const remove = async (rule) => {
    if (!await confirmDialog(`Excluir a regra "${rule.name}"?`, { confirmLabel: 'Excluir', danger: true })) return
    try { await api.del(`/automation/rules/${rule.id}`); load() }
    catch (e) { toast.error(e.message) }
  }

  const create = async () => {
    if (name.trim().length < 3) { toast.error('Dê um nome à regra (mín. 3 letras).'); return }
    setSaving(true)
    try {
      const config = action === 'retry_job' ? { max_attempts: Math.max(1, Math.min(5, maxAttempts)) } : {}
      await api.post('/automation/rules', { name: name.trim(), trigger, action, config })
      setModal(false); setName('')
      toast.success('Regra criada e ativa.')
      load()
    } catch (e) { toast.error(e.message) } finally { setSaving(false) }
  }

  const rules = data?.rules || []
  const triggers = data?.triggers || []
  const actions = data?.actions || []

  return (
    <div className="space-y-5 fade-in">
      <PageHeader title="Automações" sub="Regras SE→ENTÃO executadas de verdade pelo pipeline — nada aqui é decorativo.">
        <button className="btn btn-primary text-sm" onClick={() => setModal(true)}>+ Nova regra</button>
      </PageHeader>

      {loadError && <ErrorBanner message="Não foi possível carregar as regras." onRetry={load} />}

      {!data && !loadError ? (
        <div className="space-y-2">{Array.from({ length: 3 }).map((_, i) => <div key={i} className="h-20 skeleton rounded-card" />)}</div>
      ) : rules.length === 0 ? (
        <div className="rounded-card" style={{ background: 'var(--bg-surface)', border: '1px solid var(--border)' }}>
          <EmptyState icon="⚙" title="Nenhuma regra de automação."
            hint="Crie regras como 'quando um vídeo ficar pronto → me avisar' ou 'quando falhar → tentar de novo'."
            action={<button className="btn btn-primary text-sm" onClick={() => setModal(true)}>+ Nova regra</button>} />
        </div>
      ) : (
        <SectionCard title="Regras ativas" sub={`${rules.filter((r) => r.enabled).length} de ${rules.length} ligadas`}>
          <div className="space-y-2">
            {rules.map((r) => <RuleCard key={r.id} rule={r} onToggle={toggle} onDelete={remove} />)}
          </div>
        </SectionCard>
      )}

      <Modal open={modal} onClose={() => setModal(false)} labelledBy="rule-modal-title">
        <h3 id="rule-modal-title" className="heading text-lg font-bold mb-4">Nova regra de automação</h3>
        <div className="space-y-3">
          <label className="block space-y-1">
            <span className="text-xs font-semibold" style={{ color: 'var(--text-muted)' }}>Nome</span>
            <input className="input" value={name} onChange={(e) => setName(e.target.value)}
              placeholder="Ex.: Me avisar quando houver aprovação" maxLength={160} />
          </label>
          <label className="block space-y-1">
            <span className="text-xs font-semibold" style={{ color: 'var(--text-muted)' }}>Quando… (gatilho)</span>
            <select className="input" value={trigger} onChange={(e) => setTrigger(e.target.value)}>
              {triggers.map((t) => <option key={t.id} value={t.id}>{TRIGGER_ICON[t.id]} {t.label}</option>)}
            </select>
          </label>
          <label className="block space-y-1">
            <span className="text-xs font-semibold" style={{ color: 'var(--text-muted)' }}>Então… (ação)</span>
            <select className="input" value={action} onChange={(e) => setAction(e.target.value)}>
              {actions.map((a) => <option key={a.id} value={a.id}>{ACTION_ICON[a.id]} {a.label}</option>)}
            </select>
          </label>
          {action === 'retry_job' && (
            <label className="block space-y-1">
              <span className="text-xs font-semibold" style={{ color: 'var(--text-muted)' }}>Máximo de tentativas automáticas</span>
              <input className="input" type="number" min={1} max={5} value={maxAttempts}
                onChange={(e) => setMaxAttempts(Number(e.target.value) || 1)} />
            </label>
          )}
          {trigger === 'job_error' && action === 'retry_job' && (
            <p className="text-[11px]" style={{ color: 'var(--text-muted)' }}>
              Segurança: jobs com risco de duplicar publicação nunca são retentados automaticamente.
            </p>
          )}
          <div className="flex gap-2 justify-end pt-2">
            <button className="btn-ghost text-sm" onClick={() => setModal(false)}>Cancelar</button>
            <button className="btn btn-primary text-sm" disabled={saving} onClick={create}>
              {saving ? 'Criando…' : 'Criar regra'}
            </button>
          </div>
        </div>
      </Modal>
    </div>
  )
}
