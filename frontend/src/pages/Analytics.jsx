import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  Area, AreaChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from 'recharts'
import { api } from '../api'
import { useWs } from '../App.jsx'
import { BarMetrics } from '../components/MetricsChart.jsx'
import { fmtNum, PLATFORM_META } from '../lib'

const REFRESH_MS = 30_000
const TABS = [
  { id: 'overview', label: 'Visão geral' },
  { id: 'content', label: 'Conteúdo' },
  { id: 'learning', label: 'Aprendizado' },
  { id: 'realtime', label: 'Tempo real' },
]

/* ---------------------------------------------------------------- helpers */

function agoLabel(ts) {
  if (!ts) return '—'
  const s = Math.max(0, Math.round((Date.now() - ts) / 1000))
  if (s < 5) return 'agora mesmo'
  if (s < 60) return `há ${s}s`
  const m = Math.round(s / 60)
  if (m < 60) return `há ${m}min`
  return `há ${Math.round(m / 60)}h`
}

const dayKey = (iso) => (iso ? String(iso).slice(0, 10) : null)
const dayLabel = (k) => {
  try { return new Date(k + 'T12:00:00').toLocaleDateString('pt-BR', { day: '2-digit', month: 'short' }) }
  catch { return k }
}
const easeOut = (p) => 1 - Math.pow(1 - p, 3)

// Smoothly counts a number up to its new value on change (feels alive without faking
// data). Respects prefers-reduced-motion.
function useCountUp(target, ms = 650) {
  const [val, setVal] = useState(target || 0)
  const prev = useRef(target || 0)
  useEffect(() => {
    const to = target || 0
    const from = prev.current
    prev.current = to
    const reduce = typeof window !== 'undefined'
      && window.matchMedia?.('(prefers-reduced-motion: reduce)').matches
    if (reduce || from === to) { setVal(to); return }
    let raf, start
    const step = (t) => {
      if (!start) start = t
      const p = Math.min(1, (t - start) / ms)
      setVal(Math.round(from + (to - from) * easeOut(p)))
      if (p < 1) raf = requestAnimationFrame(step)
    }
    raf = requestAnimationFrame(step)
    return () => cancelAnimationFrame(raf)
  }, [target, ms])
  return val
}

// Build a cumulative channel-views curve from the stored snapshots. For each day we
// take, per video, its latest snapshot up to that day and sum across videos — an
// HONEST growth curve from real recorded numbers. Needs ≥2 distinct days.
function buildGrowth(snapsByJob) {
  const jobs = Object.values(snapsByJob || {}).filter((s) => s && s.length)
  if (!jobs.length) return []
  const days = new Set()
  jobs.forEach((s) => s.forEach((r) => { const d = dayKey(r.collected_at); if (d) days.add(d) }))
  const sorted = [...days].sort()
  if (sorted.length < 2) return []
  return sorted.map((day) => {
    let views = 0
    jobs.forEach((s) => {
      let best = null
      s.forEach((r) => {
        const d = dayKey(r.collected_at)
        if (d && d <= day && (!best || r.collected_at > best.collected_at)) best = r
      })
      if (best) views += best.views || 0
    })
    return { day, label: dayLabel(day), views }
  })
}

// Channel Δviews vs a baseline window (live snapshot minus the 24h/7d snapshot),
// summed across videos. Honest: no invented percentages.
function computeDelta(snapsByJob, win) {
  const order = win === '24h' ? ['24h', '2h'] : ['7d', '24h', '2h']
  let delta = 0
  let hasBaseline = false
  Object.values(snapsByJob || {}).forEach((s) => {
    if (!s || !s.length) return
    const sorted = [...s].sort((a, b) => (a.collected_at > b.collected_at ? 1 : -1))
    const live = sorted[sorted.length - 1]
    const base = order.map((t) => s.find((r) => r.snapshot_type === t)).find(Boolean) || sorted[0]
    if (base && base !== live) { hasBaseline = true; delta += Math.max(0, (live.views || 0) - (base.views || 0)) }
  })
  return { delta, hasBaseline }
}

/* ------------------------------------------------------------- components */

function LiveBadge({ connected, updatedAt }) {
  return (
    <div className="flex items-center gap-2">
      <span className="inline-flex items-center gap-1.5 badge"
        style={{ background: connected ? 'rgba(43,217,155,0.14)' : 'rgba(255,255,255,0.06)',
                 color: connected ? 'var(--success)' : 'var(--text-muted)' }}>
        <span className="relative flex h-2 w-2">
          {connected && <span className="absolute inline-flex h-full w-full rounded-full opacity-75 animate-ping"
            style={{ background: 'var(--success)' }} />}
          <span className="relative inline-flex rounded-full h-2 w-2"
            style={{ background: connected ? 'var(--success)' : 'var(--text-muted)' }} />
        </span>
        {connected ? 'AO VIVO' : 'offline'}
      </span>
      <span className="text-[11px] text-text-muted">atualizado {agoLabel(updatedAt)}</span>
    </div>
  )
}

function DeltaPill({ value, hasBaseline, win }) {
  if (!hasBaseline) {
    return <span className="text-[11px]" style={{ color: 'var(--text-dim)' }}>coletando base…</span>
  }
  const up = value > 0
  return (
    <span className="inline-flex items-center gap-1 text-[12px] font-semibold tabular-nums"
      style={{ color: up ? 'var(--success)' : 'var(--text-muted)' }}>
      {up ? '▲' : '▬'} {up ? `+${fmtNum(value)} novas` : 'estável'} · {win}
    </span>
  )
}

function HeroStat({ mode, displayName, accountsCount, views, delta, win, lastCollected }) {
  const shown = useCountUp(views)
  return (
    <div>
      <p className="text-base sm:text-lg" style={{ color: 'var(--text-muted)' }}>
        {mode === 'channel'
          ? <><span style={{ color: 'var(--text-primary)' }}>{displayName}</span> acumulou</>
          : <>Seus <span style={{ color: 'var(--text-primary)' }}>{accountsCount}</span> canais somaram</>}
      </p>
      <div className="flex flex-wrap items-end gap-x-4 gap-y-1 mt-0.5">
        <span className="heading font-black tabular-nums leading-none"
          style={{ fontSize: 'clamp(40px, 6vw, 60px)', color: 'var(--text-primary)' }}>
          {fmtNum(shown)}
        </span>
        <span className="text-base pb-2" style={{ color: 'var(--text-muted)' }}>visualizações</span>
        <span className="pb-2.5"><DeltaPill value={delta.delta} hasBaseline={delta.hasBaseline} win={win} /></span>
      </div>
      <p className="text-[11px] mt-1" style={{ color: 'var(--text-dim)' }}>
        Atualiza a cada 8 min · última coleta {agoLabel(lastCollected)}
      </p>
    </div>
  )
}

function KpiTile({ label, value, suffix, delta }) {
  const shown = useCountUp(typeof value === 'number' ? value : 0)
  return (
    <div className="rounded-card p-4" style={{ background: 'var(--bg-surface)', border: '1px solid var(--border)' }}>
      <p className="text-[11px] uppercase tracking-[0.08em] mb-1.5" style={{ color: 'var(--text-muted)' }}>{label}</p>
      <p className="heading text-3xl font-black tabular-nums leading-none" style={{ color: 'var(--text-primary)' }}>
        {suffix ? value : fmtNum(shown)}<span className="text-lg font-bold" style={{ color: 'var(--text-muted)' }}>{suffix}</span>
      </p>
      {delta && <p className="mt-2"><DeltaPill value={delta.value} hasBaseline={delta.hasBaseline} win={delta.win} /></p>}
    </div>
  )
}

function KpiRow({ totals, delta, win, engagement }) {
  return (
    <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
      <KpiTile label="Visualizações" value={totals.views || 0}
        delta={{ value: delta.delta, hasBaseline: delta.hasBaseline, win }} />
      <KpiTile label="Likes" value={totals.likes || 0} />
      <KpiTile label="Comentários" value={totals.comments || 0} />
      <KpiTile label="Engajamento" value={engagement} suffix="%" />
    </div>
  )
}

function Sparkline({ growth }) {
  const bars = useMemo(() => {
    if (!growth || growth.length < 2) return []
    return growth.map((g, i) => (i === 0 ? 0 : Math.max(0, g.views - growth[i - 1].views))).slice(1)
  }, [growth])
  const max = Math.max(1, ...bars)
  if (!bars.length) return null
  return (
    <div className="flex items-end gap-[3px] h-12 mt-2">
      {bars.map((b, i) => (
        <div key={i} className="flex-1 rounded-sm transition-all" style={{
          height: `${Math.max(6, Math.round((b / max) * 100))}%`,
          background: i === bars.length - 1 ? 'var(--accent)' : 'rgba(124,106,255,0.55)',
        }} />
      ))}
    </div>
  )
}

function Thumb({ platform }) {
  const pm = PLATFORM_META[platform] || PLATFORM_META.youtube
  return (
    <span className="inline-flex items-center justify-center rounded-lg shrink-0"
      style={{ width: 38, height: 38, background: 'var(--bg-elevated)', color: pm.color, fontSize: 16 }}>
      {pm.icon}
    </span>
  )
}

function RealtimePanel({ connected, updatedAt, videos, growth, recentJob }) {
  const top = (videos || []).slice(0, 5)
  const liveViews = (videos || []).reduce((a, v) => a + (v.views || 0), 0)
  const shown = useCountUp(liveViews)
  return (
    <div className="rounded-card p-4" style={{ background: 'var(--bg-surface)', border: '1px solid var(--border)' }}>
      <div className="flex items-center justify-between">
        <LiveBadge connected={connected} updatedAt={updatedAt} />
      </div>
      <div className="mt-3">
        <p className="heading text-3xl font-black tabular-nums leading-none" style={{ color: 'var(--text-primary)' }}>{fmtNum(shown)}</p>
        <p className="text-[12px]" style={{ color: 'var(--text-muted)' }}>visualizações · ao vivo</p>
        <Sparkline growth={growth} />
        {growth.length >= 2 && <p className="text-[10px] text-right -mt-1" style={{ color: 'var(--text-dim)' }}>agora</p>}
      </div>
      <div className="mt-3 pt-3" style={{ borderTop: '1px solid rgba(255,255,255,0.06)' }}>
        <p className="text-[11px] uppercase tracking-[0.08em] mb-2" style={{ color: 'var(--text-muted)' }}>Conteúdo principal</p>
        {top.length === 0
          ? <p className="text-xs py-2" style={{ color: 'var(--text-dim)' }}>Sem vídeos ainda.</p>
          : (
            <ul className="space-y-2">
              {top.map((v) => {
                const flash = recentJob === v.job_id
                return (
                  <li key={v.job_id} className="flex items-center gap-2.5 rounded-lg px-1.5 py-1 transition-colors"
                    style={{ background: flash ? 'rgba(43,217,155,0.12)' : 'transparent' }}>
                    <Thumb platform={v.platform || 'youtube'} />
                    <span className="flex-1 min-w-0">
                      <span className="block text-xs truncate" title={v.title}>{v.title || `Job ${v.job_id}`}</span>
                    </span>
                    <span className="text-xs font-semibold tabular-nums" style={{ color: 'var(--success)' }}>{fmtNum(v.views)}</span>
                  </li>
                )
              })}
            </ul>
          )}
      </div>
    </div>
  )
}

function GrowthChart({ growth, byPlatform, mode }) {
  if (growth.length >= 2) {
    return (
      <ResponsiveContainer width="100%" height={260}>
        <AreaChart data={growth} margin={{ top: 8, right: 8, bottom: 0, left: -10 }}>
          <defs>
            <linearGradient id="gViews" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor="var(--accent)" stopOpacity={0.30} />
              <stop offset="100%" stopColor="var(--accent)" stopOpacity={0} />
            </linearGradient>
          </defs>
          <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.05)" vertical={false} />
          <XAxis dataKey="label" stroke="var(--text-muted)" fontSize={11} tickLine={false} axisLine={false} />
          <YAxis stroke="var(--text-muted)" fontSize={11} tickLine={false} axisLine={false}
            tickFormatter={fmtNum} width={48} />
          <Tooltip
            contentStyle={{ background: 'var(--bg-elevated)', border: '1px solid var(--border)', borderRadius: 8, color: 'var(--text-primary)' }}
            labelStyle={{ color: 'var(--text-muted)' }} formatter={(v) => [fmtNum(v), 'views']} />
          <Area type="monotone" dataKey="views" stroke="var(--accent)" strokeWidth={2}
            fill="url(#gViews)" dot={false} activeDot={{ r: 4, fill: 'var(--accent)', stroke: 'var(--bg-base)', strokeWidth: 2 }} />
        </AreaChart>
      </ResponsiveContainer>
    )
  }
  if (mode === 'global' && byPlatform.length > 0) {
    return <BarMetrics data={byPlatform} x="platform" y="views" />
  }
  return (
    <p className="text-text-muted text-sm py-10 text-center">
      Sem histórico ainda. A curva aparece depois das coletas (2h / 24h / 7d).
    </p>
  )
}

function TopContentTable({ videos, limit }) {
  if (videos === null) {
    return <div className="space-y-2">{[1, 2, 3].map((i) => <div key={i} className="skeleton h-12 rounded-card" />)}</div>
  }
  if (!videos.length) {
    return <p className="text-text-muted text-sm py-8 text-center">Nenhum vídeo publicado ainda. Assim que um for ao ar, aparece aqui com as métricas.</p>
  }
  const rows = limit ? videos.slice(0, limit) : videos
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="text-text-muted text-[11px] uppercase tracking-wider text-left">
            <th className="pb-3 pr-2 font-medium w-8">#</th>
            <th className="pb-3 pr-3 font-medium">Vídeo</th>
            <th className="pb-3 px-3 font-medium">Tipo</th>
            <th className="pb-3 px-3 font-medium text-right">Views</th>
            <th className="pb-3 px-3 font-medium text-right">Likes</th>
            <th className="pb-3 px-3 font-medium text-right">Coment.</th>
            <th className="pb-3 pl-3 font-medium" />
          </tr>
        </thead>
        <tbody>
          {rows.map((v, i) => (
            <tr key={v.job_id} className="border-t transition-colors hover:bg-white/[0.03]" style={{ borderColor: 'rgba(255,255,255,0.06)' }}>
              <td className="py-3 pr-2 tabular-nums" style={{ color: 'var(--text-dim)' }}>{i + 1}</td>
              <td className="py-3 pr-3 max-w-[300px]">
                <div className="flex items-center gap-2.5">
                  <Thumb platform={v.platform || 'youtube'} />
                  <div className="min-w-0">
                    <p className="truncate font-medium" title={v.title}>{v.title || `Job ${v.job_id}`}</p>
                    {v.snapshots === 0
                      ? <span className="text-[10px]" style={{ color: 'var(--text-dim)' }}>métricas em coleta…</span>
                      : v.published_at && <span className="text-[10px]" style={{ color: 'var(--text-dim)' }}>{agoLabel(Date.parse(v.published_at))}</span>}
                  </div>
                </div>
              </td>
              <td className="py-3 px-3 whitespace-nowrap">
                <span className="badge text-[10px]" style={{ background: 'var(--accent-dim)', color: 'var(--accent)' }}>
                  {(v.content_type || '—').replace(/_/g, ' ')}
                </span>
              </td>
              <td className="py-3 px-3 text-right font-semibold tabular-nums">{fmtNum(v.views)}</td>
              <td className="py-3 px-3 text-right tabular-nums" style={{ color: 'var(--text-muted)' }}>{fmtNum(v.likes)}</td>
              <td className="py-3 px-3 text-right tabular-nums" style={{ color: 'var(--text-muted)' }}>{fmtNum(v.comments)}</td>
              <td className="py-3 pl-3 text-right">
                {v.youtube_url && <a href={v.youtube_url} target="_blank" rel="noreferrer" className="text-xs text-accent hover:underline whitespace-nowrap">YouTube ↗</a>}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function LearningBrief({ insights, bestTimes }) {
  if (!insights) return null
  if (!insights.ready) {
    const need = Math.max(0, 3 - (insights.n || 0))
    const pct = Math.min(100, Math.round(((insights.n || 0) / 3) * 100))
    return (
      <div className="rounded-card p-6" style={{ background: 'var(--bg-surface)', border: '1px solid rgba(124,106,255,0.32)' }}>
        <h3 className="heading font-semibold mb-1">🧠 Inteligência do canal</h3>
        <p className="text-sm text-text-muted">
          O sistema começa a otimizar este canal depois de <strong>3 vídeos com métricas reais</strong>.
          {need > 0 ? ` Faltam ${need}.` : ''}
        </p>
        <div className="mt-3 h-2 rounded-full overflow-hidden" style={{ background: 'var(--bg-elevated)' }}>
          <div className="h-full rounded-full transition-all duration-700" style={{ width: `${pct}%`, background: 'var(--grad-accent)' }} />
        </div>
        <p className="text-[11px] text-text-muted mt-1.5">{insights.n || 0}/3 vídeos medidos</p>
      </div>
    )
  }
  const maxAvg = Math.max(1, ...(insights.type_ranking || []).map((t) => t.avg_views))
  const times = (bestTimes?.suggested || []).map((iso) => {
    try { return new Date(iso).toLocaleString('pt-BR', { weekday: 'short', hour: '2-digit', minute: '2-digit' }) }
    catch { return iso }
  })
  return (
    <div className="rounded-card p-6 space-y-5" style={{ background: 'var(--bg-surface)', border: '1px solid rgba(124,106,255,0.32)' }}>
      <div className="flex items-center gap-2 flex-wrap">
        <h3 className="heading font-semibold text-lg">🧠 Inteligência do canal</h3>
        <span className="badge" style={{ background: 'rgba(43,217,155,0.14)', color: 'var(--success)' }}>
          aprendendo · {insights.n} vídeos
        </span>
      </div>

      {insights.best_type && (
        <div className="flex flex-wrap gap-2">
          <span className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm font-semibold"
            style={{ background: 'rgba(43,217,155,0.12)', color: 'var(--success)' }}>
            ▲ Formato campeão: {(insights.best_type || '').replace(/_/g, ' ')}
          </span>
          {insights.worst_type && (
            <span className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm"
              style={{ background: 'var(--bg-elevated)', color: 'var(--text-muted)' }}>
              ⛔ Evite priorizar: {(insights.worst_type || '').replace(/_/g, ' ')}
            </span>
          )}
        </div>
      )}

      {(insights.type_ranking || []).length > 0 && (
        <div className="space-y-2">
          <p className="text-[11px] uppercase tracking-wider text-text-muted">Média de views por formato</p>
          {insights.type_ranking.map((t) => (
            <div key={t.type} className="flex items-center gap-3">
              <span className="text-xs w-40 shrink-0 truncate" title={t.type}>{(t.type || '').replace(/_/g, ' ')}</span>
              <div className="flex-1 h-2.5 rounded-full overflow-hidden" style={{ background: 'var(--bg-elevated)' }}>
                <div className="h-full rounded-full transition-all duration-700" style={{ width: `${Math.round((t.avg_views / maxAvg) * 100)}%`, background: 'var(--grad-accent)' }} />
              </div>
              <span className="text-xs tabular-nums w-20 text-right text-text-muted">{fmtNum(t.avg_views)} · {t.n}v</span>
            </div>
          ))}
        </div>
      )}

      <div className="grid sm:grid-cols-2 gap-4">
        {(insights.top_titles || []).length > 0 && (
          <div>
            <p className="text-[11px] uppercase tracking-wider mb-1.5" style={{ color: 'var(--success)' }}>✅ Replicar (mais views)</p>
            <ul className="space-y-1">
              {insights.top_titles.slice(0, 5).map((t, i) => (
                <li key={i} className="text-xs text-text-muted truncate" title={t}>{t}</li>
              ))}
            </ul>
          </div>
        )}
        {(insights.bottom_titles || []).length > 0 && (
          <div>
            <p className="text-[11px] uppercase tracking-wider mb-1.5" style={{ color: 'var(--error)' }}>⛔ Evitar (menos views)</p>
            <ul className="space-y-1">
              {insights.bottom_titles.slice(0, 5).map((t, i) => (
                <li key={i} className="text-xs text-text-muted truncate" title={t}>{t}</li>
              ))}
            </ul>
          </div>
        )}
      </div>

      {(insights.best_tags || []).length > 0 && (
        <div>
          <p className="text-[11px] uppercase tracking-wider text-text-muted mb-1.5">Tags que puxam audiência</p>
          <div className="flex flex-wrap gap-1.5">
            {insights.best_tags.slice(0, 12).map((t) => (
              <span key={t} className="badge text-[11px]" style={{ background: 'rgba(124,106,255,0.12)', color: 'var(--accent)' }}>{t}</span>
            ))}
          </div>
        </div>
      )}

      {times.length > 0 && (
        <div>
          <p className="text-[11px] uppercase tracking-wider text-text-muted mb-1.5">⏰ Melhores horários aprendidos</p>
          <div className="flex flex-wrap gap-1.5">
            {times.map((t, i) => (
              <span key={i} className="badge text-[11px]" style={{ background: 'var(--bg-elevated)' }}>{t}</span>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}

function SectionCard({ title, sub, children }) {
  return (
    <div className="rounded-card p-5" style={{ background: 'var(--bg-surface)', border: '1px solid var(--border)' }}>
      <div className="flex items-center gap-3 mb-4">
        <h3 className="heading font-semibold">{title}</h3>
        {sub && <span className="text-text-muted text-xs">{sub}</span>}
        <div className="flex-1 h-px" style={{ background: 'rgba(255,255,255,0.06)' }} />
      </div>
      {children}
    </div>
  )
}

/* -------------------------------------------------------------------- page */

export default function Analytics() {
  const { connected, count, events } = useWs()
  const [accounts, setAccounts] = useState([])
  const [selected, setSelected] = useState(null)      // null = Todos
  const [tab, setTab] = useState('overview')
  const [win, setWin] = useState('7d')                // delta window: 24h | 7d
  const [overview, setOverview] = useState(null)
  const [videos, setVideos] = useState(null)
  const [insights, setInsights] = useState(null)
  const [bestTimes, setBestTimes] = useState(null)
  const [snaps, setSnaps] = useState({})              // { [jobId]: snapshots[] }
  const [updatedAt, setUpdatedAt] = useState(null)
  const [recentJob, setRecentJob] = useState(null)
  const [, setTick] = useState(0)

  useEffect(() => {
    api.get('/accounts').then((d) => setAccounts(d.accounts || [])).catch(() => setAccounts([]))
  }, [])

  const load = useCallback(async () => {
    let vid = []
    if (selected) {
      const [v, ins, bt] = await Promise.all([
        api.get(`/analytics/videos?account_id=${selected}`).then((r) => r.videos || []).catch(() => []),
        api.get(`/analytics/insights/${selected}`).catch(() => null),
        api.get(`/analytics/best-times/${selected}`).catch(() => null),
      ])
      vid = v
      setVideos(v); setInsights(ins); setBestTimes(bt); setOverview(null)
    } else {
      const [ov, v] = await Promise.all([
        api.get('/analytics/overview').catch(() => ({ totals: {}, by_platform: {} })),
        api.get('/analytics/videos').then((r) => r.videos || []).catch(() => []),
      ])
      vid = v
      setOverview(ov); setVideos(v); setInsights(null); setBestTimes(null)
    }
    // Pull snapshot histories for the top videos → powers the growth curve + deltas.
    const topJobs = (vid || []).slice(0, 12)
    const entries = await Promise.all(topJobs.map((v) =>
      api.get(`/analytics/job/${v.job_id}`).then((r) => [v.job_id, r.snapshots || []]).catch(() => [v.job_id, []])))
    setSnaps(Object.fromEntries(entries))
    setUpdatedAt(Date.now())
  }, [selected])

  useEffect(() => { load() }, [load])
  useEffect(() => { const t = setInterval(load, REFRESH_MS); return () => clearInterval(t) }, [load])
  useEffect(() => { const t = setInterval(() => setTick((n) => n + 1), 1000); return () => clearInterval(t) }, [])
  // Instant refresh + "flash" the video whose numbers just landed.
  useEffect(() => {
    const last = events && events[events.length - 1]
    if (last && typeof last.type === 'string' && last.type.startsWith('analytics')) {
      if (last.job_id) { setRecentJob(last.job_id); setTimeout(() => setRecentJob(null), 1500) }
      load()
    }
  }, [count]) // eslint-disable-line react-hooks/exhaustive-deps

  const totals = useMemo(() => {
    if (!selected) return overview?.totals || {}
    const vs = videos || []
    return {
      views: vs.reduce((a, v) => a + (v.views || 0), 0),
      likes: vs.reduce((a, v) => a + (v.likes || 0), 0),
      comments: vs.reduce((a, v) => a + (v.comments || 0), 0),
      shares: 0,
    }
  }, [selected, overview, videos])

  const growth = useMemo(() => buildGrowth(snaps), [snaps])
  const delta = useMemo(() => computeDelta(snaps, win), [snaps, win])
  const engagement = useMemo(() => {
    const v = totals.views || 0
    if (!v) return '0,0'
    return (((totals.likes || 0) + (totals.comments || 0)) / v * 100).toFixed(1).replace('.', ',')
  }, [totals])

  const byPlatform = Object.entries(overview?.by_platform || {}).map(([platform, views]) => ({ platform, views }))
  const selAcct = accounts.find((a) => a.id === selected)
  const mode = selected ? 'channel' : 'global'
  const lastCollected = useMemo(() => {
    const ts = (videos || []).map((v) => (v.last_collected ? Date.parse(v.last_collected) : 0)).filter(Boolean)
    return ts.length ? Math.max(...ts) : updatedAt
  }, [videos, updatedAt])

  const hero = (
    <HeroStat mode={mode} displayName={selAcct?.display_name} accountsCount={accounts.length}
      views={totals.views || 0} delta={delta} win={win} lastCollected={lastCollected} />
  )
  const realtime = (
    <RealtimePanel connected={connected} updatedAt={updatedAt} videos={videos}
      growth={growth} recentJob={recentJob} />
  )

  return (
    <div className="space-y-5 fade-in">
      {/* Header */}
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="heading text-2xl text-gradient font-bold">Analytics</h2>
          <p className="text-text-muted text-sm">
            {mode === 'channel' ? `Detalhe do canal ${selAcct?.display_name || ''}.`
                                : 'Visão geral de todos os canais. Clique num canal para ver o detalhe.'}
          </p>
        </div>
        <div className="flex items-center gap-3">
          {/* Janela de comparação */}
          <div className="inline-flex rounded-btn overflow-hidden" style={{ border: '1px solid var(--border)' }}>
            {['24h', '7d'].map((w) => (
              <button key={w} onClick={() => setWin(w)}
                className="px-2.5 py-1 text-[11px] font-semibold transition-colors"
                style={w === win ? { background: 'var(--accent)', color: '#fff' } : { background: 'transparent', color: 'var(--text-muted)' }}>
                vs {w}
              </button>
            ))}
          </div>
          <LiveBadge connected={connected} updatedAt={updatedAt} />
        </div>
      </div>

      {/* Seletor de canal */}
      <div className="flex gap-2 overflow-x-auto pb-1 -mx-1 px-1">
        <button onClick={() => setSelected(null)}
          className="badge shrink-0 cursor-pointer transition-colors"
          style={selected === null ? { background: 'var(--accent)', color: '#fff' } : { background: 'var(--bg-elevated)', color: 'var(--text-muted)' }}>
          Todos
        </button>
        {accounts.map((a) => {
          const pm = PLATFORM_META[a.platform]
          const on = selected === a.id
          return (
            <button key={a.id} onClick={() => setSelected(a.id)}
              className="badge shrink-0 cursor-pointer transition-colors inline-flex items-center gap-1.5"
              style={on ? { background: 'var(--accent)', color: '#fff' } : { background: 'var(--bg-elevated)', color: 'var(--text-muted)' }}>
              {pm && <span style={{ color: on ? '#fff' : pm.color }}>{pm.icon}</span>}
              <span className="max-w-[140px] truncate">{a.display_name}</span>
            </button>
          )
        })}
      </div>

      {/* Tabs */}
      <div className="flex gap-1 overflow-x-auto" style={{ borderBottom: '1px solid rgba(255,255,255,0.06)' }}>
        {TABS.map((t) => {
          const on = tab === t.id
          return (
            <button key={t.id} onClick={() => setTab(t.id)}
              className="px-3.5 py-2 text-sm font-medium shrink-0 transition-colors relative"
              style={{ color: on ? 'var(--text-primary)' : 'var(--text-muted)' }}>
              {t.label}
              {on && <span className="absolute left-2 right-2 -bottom-px h-0.5 rounded-full" style={{ background: 'var(--accent)' }} />}
            </button>
          )
        })}
      </div>

      {/* ---- Tab content ---- */}
      {tab === 'overview' && (
        <div className="space-y-5">
          {hero}
          <KpiRow totals={totals} delta={delta} win={win} engagement={engagement} />
          <div className="grid grid-cols-1 xl:grid-cols-12 gap-5">
            <div className="xl:col-span-8 space-y-5">
              <SectionCard title="Crescimento de visualizações" sub={mode === 'global' ? 'todos os canais' : selAcct?.display_name}>
                <GrowthChart growth={growth} byPlatform={byPlatform} mode={mode} />
              </SectionCard>
              <SectionCard title="Conteúdo principal" sub="mais vistos">
                <TopContentTable videos={videos} limit={6} />
              </SectionCard>
            </div>
            <div className="xl:col-span-4">
              <div className="xl:sticky xl:top-4">{realtime}</div>
            </div>
          </div>
          {mode === 'channel' && <LearningBrief insights={insights} bestTimes={bestTimes} />}
        </div>
      )}

      {tab === 'content' && (
        <div className="space-y-5">
          <KpiRow totals={totals} delta={delta} win={win} engagement={engagement} />
          <SectionCard title="Desempenho por vídeo" sub="cada vídeo separadamente">
            <TopContentTable videos={videos} />
          </SectionCard>
        </div>
      )}

      {tab === 'learning' && (
        <div className="space-y-5">
          {mode === 'channel'
            ? <LearningBrief insights={insights} bestTimes={bestTimes} />
            : (
              <div className="rounded-card p-8 text-center" style={{ background: 'var(--bg-surface)', border: '1px solid var(--border)' }}>
                <p className="text-text-muted text-sm">Selecione um canal acima para ver o que o sistema aprendeu sobre ele.</p>
              </div>
            )}
        </div>
      )}

      {tab === 'realtime' && (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-5">
          {realtime}
          <SectionCard title="Conteúdo principal" sub="ao vivo">
            <TopContentTable videos={videos} limit={10} />
          </SectionCard>
        </div>
      )}
    </div>
  )
}
