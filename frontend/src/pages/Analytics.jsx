import { useEffect, useState } from 'react'
import { api } from '../api'
import { BarMetrics } from '../components/MetricsChart.jsx'
import { fmtNum } from '../lib'

export default function Analytics() {
  const [overview, setOverview] = useState(null)

  useEffect(() => { api.get('/analytics/overview').then(setOverview).catch(() => setOverview({ totals: {}, by_platform: {} })) }, [])

  if (!overview) return (
    <div className="space-y-6 fade-in">
      <div className="h-8 w-48 skeleton rounded-card" />
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        {[1,2,3,4].map(i => <div key={i} className="card skeleton h-28" />)}
      </div>
      <div className="card skeleton h-48" />
    </div>
  )

  const t = overview.totals || {}
  const cards = [
    { label: 'Views', value: fmtNum(t.views), icon: '👁' },
    { label: 'Likes', value: fmtNum(t.likes), icon: '❤️' },
    { label: 'Comentários', value: fmtNum(t.comments), icon: '💬' },
    { label: 'Compart.', value: fmtNum(t.shares), icon: '🔁' },
  ]
  const byPlatform = Object.entries(overview.by_platform || {}).map(([platform, views]) => ({ platform, views }))
  const hasData = (t.views || 0) > 0 || byPlatform.length > 0

  return (
    <div className="space-y-6 fade-in">
      <div>
        <h2 className="heading text-2xl text-gradient font-bold">Analytics</h2>
        <p className="text-text-muted text-sm">Métricas agregadas de todas as plataformas conectadas.</p>
      </div>

      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        {cards.map((c) => (
          <div key={c.label} className="card-stat p-6">
            <div className="relative overflow-hidden">
              <span className="absolute right-3 top-3 text-5xl opacity-10 select-none">{c.icon}</span>
              <p className="text-text-muted text-xs uppercase tracking-wider mb-2">{c.label}</p>
              <p className="heading text-3xl font-black">{c.value}</p>
            </div>
          </div>
        ))}
      </div>

      <div className="card p-5">
        <div className="flex items-center gap-3 mb-4">
          <h3 className="heading font-semibold">Views por plataforma</h3>
          <div className="flex-1 h-px" style={{ background: 'rgba(255,255,255,0.06)' }} />
        </div>
        {hasData && byPlatform.length > 0
          ? <BarMetrics data={byPlatform} x="platform" y="views" />
          : <p className="text-text-muted text-sm py-8 text-center">Sem métricas ainda. Os dados aparecem após a publicação (coletados em 2h / 24h / 7d).</p>}
      </div>

      <div className="card p-5">
        <div className="flex items-center gap-3 mb-2">
          <h3 className="heading font-semibold">Thumbnail A/B</h3>
          <span className="badge text-[10px]" style={{ background: 'rgba(255,182,39,0.15)', color: 'var(--warning)' }}>Em breve</span>
          <div className="flex-1 h-px" style={{ background: 'rgba(255,255,255,0.06)' }} />
        </div>
        <p className="text-text-muted text-sm">A comparação de CTR entre as variantes A e B aparece aqui após a coleta de analytics das contas conectadas.</p>
      </div>
    </div>
  )
}
