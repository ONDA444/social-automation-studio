import { useEffect, useState } from 'react'
import { api } from '../api'
import { BarMetrics } from '../components/MetricsChart.jsx'
import { fmtNum } from '../lib'

export default function Analytics() {
  const [overview, setOverview] = useState(null)

  useEffect(() => { api.get('/analytics/overview').then(setOverview).catch(() => setOverview({ totals: {}, by_platform: {} })) }, [])
  if (!overview) return <p className="text-text-muted">Carregando…</p>

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
    <div className="space-y-6">
      <h2 className="heading text-2xl font-semibold">Analytics</h2>

      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        {cards.map((c) => (
          <div key={c.label} className="card p-4">
            <div className="flex items-center justify-between"><span className="text-2xl">{c.icon}</span><span className="heading text-2xl font-bold">{c.value}</span></div>
            <p className="text-xs text-text-muted mt-1">{c.label}</p>
          </div>
        ))}
      </div>

      <div className="card p-5">
        <h3 className="heading font-semibold mb-4">Views por plataforma</h3>
        {hasData && byPlatform.length > 0
          ? <BarMetrics data={byPlatform} x="platform" y="views" />
          : <p className="text-text-muted text-sm py-8 text-center">Sem métricas ainda. Os dados aparecem após a publicação (coletados em 2h / 24h / 7d).</p>}
      </div>

      <div className="card p-5">
        <h3 className="heading font-semibold mb-2">Thumbnail A/B</h3>
        <p className="text-text-muted text-sm">A comparação de CTR entre as variantes A e B aparece aqui após a coleta de analytics das contas conectadas.</p>
      </div>
    </div>
  )
}
