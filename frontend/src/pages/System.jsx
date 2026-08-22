import { useCallback, useEffect, useState } from 'react'
import { api } from '../api'
import { PageHeader, SectionCard, ErrorBanner, EmptyState } from '../components/ui.jsx'

const STATUS_STYLE = {
  green: { color: 'var(--success)', label: 'OK' },
  yellow: { color: 'var(--warning)', label: 'Atenção' },
  red: { color: 'var(--error)', label: 'Falha' },
}

const SEVERITY_STYLE = {
  ERROR: 'var(--error)',
  WARNING: 'var(--warning)',
  SUCCESS: 'var(--success)',
  INFO: 'var(--accent-blue)',
  DEBUG: 'var(--text-dim)',
}

const LEVELS = ['', 'ERROR', 'WARNING', 'SUCCESS', 'INFO']

function HealthCard({ name, check }) {
  const meta = STATUS_STYLE[check.status] || STATUS_STYLE.yellow
  return (
    <div className="rounded-card p-4" style={{ background: 'var(--bg-surface)', border: '1px solid var(--border)' }}>
      <div className="flex items-center gap-2">
        <span className="w-2.5 h-2.5 rounded-full shrink-0" style={{ background: meta.color }} />
        <p className="text-sm font-bold truncate" style={{ color: 'var(--text-primary)' }}>{name}</p>
        <span className="ml-auto badge text-[10px]"
          style={{ background: `color-mix(in srgb, ${meta.color} 16%, transparent)`, color: meta.color, border: 'none' }}>
          {meta.label}
        </span>
      </div>
      {check.detail && (
        <p className="text-xs mt-2 break-words" style={{ color: 'var(--text-muted)' }}>{check.detail}</p>
      )}
    </div>
  )
}

export default function System() {
  const [health, setHealth] = useState(null)
  const [healthErr, setHealthErr] = useState(null)
  const [logs, setLogs] = useState([])
  const [logsNote, setLogsNote] = useState(null)
  const [level, setLevel] = useState('')
  const [live, setLive] = useState(true)

  const loadHealth = useCallback(() => {
    api.get('/health')
      .then((d) => { setHealth(d); setHealthErr(null) })
      .catch((e) => setHealthErr(e.message))
  }, [])

  const loadLogs = useCallback(() => {
    const qs = level ? `?level=${level}&limit=150` : '?limit=150'
    api.get(`/system/logs${qs}`)
      .then((d) => { setLogs(d.events || []); setLogsNote(d.note || null) })
      .catch(() => setLogsNote('não foi possível carregar os logs'))
  }, [level])

  useEffect(() => { loadHealth() }, [loadHealth])
  useEffect(() => { loadLogs() }, [loadLogs])

  useEffect(() => {
    if (!live) return
    const t = setInterval(() => { loadHealth(); loadLogs() }, 8000)
    return () => clearInterval(t)
  }, [live, loadHealth, loadLogs])

  const checks = health?.checks || {}
  const overall = health?.status

  return (
    <div className="space-y-6 fade-in">
      <PageHeader title="Saúde do Sistema" sub="componentes, deploy e logs estruturados dos agentes">
        <label className="flex items-center gap-2 text-xs" style={{ color: 'var(--text-muted)' }}>
          <input type="checkbox" checked={live} onChange={(e) => setLive(e.target.checked)} />
          Atualização automática
        </label>
      </PageHeader>

      {healthErr && <ErrorBanner message={healthErr} onRetry={loadHealth} />}

      <SectionCard
        title="Componentes"
        sub={overall ? `status geral: ${overall}` : undefined}
        action={<button className="btn-ghost btn-sm" onClick={loadHealth}>↻ Atualizar</button>}
      >
        {!health && !healthErr
          ? <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
              {Array.from({ length: 6 }).map((_, i) => <div key={i} className="h-20 skeleton rounded-card" />)}
            </div>
          : (
            <>
              <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
                {Object.entries(checks).map(([name, check]) => (
                  <HealthCard key={name} name={name} check={check} />
                ))}
              </div>
              {health?.deploy?.commit && (
                <p className="text-[11px] mt-4 font-mono" style={{ color: 'var(--text-dim)' }}>
                  deploy: {health.deploy.commit}
                  {health.deploy.branch ? ` (${health.deploy.branch})` : ''}
                  {health.deploy.deployed_at ? ` · ${health.deploy.deployed_at}` : ''}
                </p>
              )}
            </>
          )}
      </SectionCard>

      <SectionCard
        title="Logs dos agentes"
        sub="eventos estruturados — mais recentes primeiro"
        action={(
          <div className="flex items-center gap-2">
            <select className="input text-xs py-1.5" value={level} onChange={(e) => setLevel(e.target.value)} aria-label="Filtrar por severidade">
              {LEVELS.map((l) => <option key={l} value={l}>{l || 'Todos'}</option>)}
            </select>
            <button className="btn-ghost btn-sm" onClick={loadLogs}>↻</button>
          </div>
        )}
      >
        {logsNote && logs.length === 0
          ? <EmptyState icon="🧾" title={logsNote} />
          : logs.length === 0
            ? <EmptyState icon="🧾" title="Nenhum evento para este filtro." hint="Troque a severidade ou aguarde novos eventos dos agentes." />
            : (
              <div className="space-y-1 max-h-[480px] overflow-y-auto font-mono text-xs">
                {logs.map((ev, i) => {
                  const color = SEVERITY_STYLE[ev.severity] || SEVERITY_STYLE.INFO
                  return (
                    <div key={i} className="flex items-start gap-2 px-2 py-1.5 rounded-btn"
                      style={{ background: i % 2 ? 'transparent' : 'var(--bg-elevated)' }}>
                      <span className="shrink-0 w-[72px] font-bold" style={{ color }}>{ev.severity}</span>
                      <span className="shrink-0" style={{ color: 'var(--text-dim)' }}>
                        {ev.job_id != null ? `#${ev.job_id}` : '—'}
                      </span>
                      <span className="shrink-0 font-semibold" style={{ color: 'var(--text-muted)' }}>{ev.agent}</span>
                      <span className="break-words min-w-0" style={{ color: 'var(--text-primary)' }}>{ev.message}</span>
                    </div>
                  )
                })}
              </div>
            )}
      </SectionCard>
    </div>
  )
}
