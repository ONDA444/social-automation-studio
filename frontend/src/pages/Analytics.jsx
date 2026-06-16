import { useEffect, useState } from 'react'
import { api } from '../api'
import { BarMetrics } from '../components/MetricsChart.jsx'
import { fmtNum } from '../lib'

export default function Analytics() {
  const [overview, setOverview] = useState(null)
  const [videos, setVideos] = useState(null)

  useEffect(() => {
    api.get('/analytics/overview').then(setOverview).catch(() => setOverview({ totals: {}, by_platform: {} }))
    api.get('/analytics/videos').then((r) => setVideos(r.videos || [])).catch(() => setVideos([]))
  }, [])

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
          <div key={c.label} className="card-stat p-4 sm:p-6">
            <div className="relative overflow-hidden">
              <span className="absolute right-2 top-2 sm:right-3 sm:top-3 text-4xl sm:text-5xl opacity-10 select-none">{c.icon}</span>
              <p className="text-text-muted text-xs uppercase tracking-wider mb-2">{c.label}</p>
              <p className="heading text-2xl sm:text-3xl font-black">{c.value}</p>
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
        <div className="flex items-center gap-3 mb-4">
          <h3 className="heading font-semibold">Desempenho por vídeo</h3>
          <span className="text-text-muted text-xs">cada vídeo separadamente</span>
          <div className="flex-1 h-px" style={{ background: 'rgba(255,255,255,0.06)' }} />
        </div>
        {videos === null
          ? <div className="space-y-2">{[1,2,3].map(i => <div key={i} className="skeleton h-10 rounded-card" />)}</div>
          : videos.length === 0
            ? <p className="text-text-muted text-sm py-8 text-center">Nenhum vídeo publicado ainda. Assim que um vídeo for ao ar, ele aparece aqui com as métricas (coletadas em 2h / 24h / 7d).</p>
            : (
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="text-text-muted text-xs uppercase tracking-wider text-left">
                      <th className="pb-3 pr-3 font-medium">Vídeo</th>
                      <th className="pb-3 px-3 font-medium">Tipo</th>
                      <th className="pb-3 px-3 font-medium text-right">Views</th>
                      <th className="pb-3 px-3 font-medium text-right">Likes</th>
                      <th className="pb-3 px-3 font-medium text-right">Coment.</th>
                      <th className="pb-3 pl-3 font-medium" />
                    </tr>
                  </thead>
                  <tbody>
                    {videos.map((v) => (
                      <tr key={v.job_id} className="border-t" style={{ borderColor: 'rgba(255,255,255,0.06)' }}>
                        <td className="py-3 pr-3 max-w-[280px]">
                          <p className="truncate font-medium" title={v.title}>{v.title || `Job ${v.job_id}`}</p>
                          {v.snapshots === 0 && <span className="text-text-muted text-[10px]">métricas em coleta…</span>}
                        </td>
                        <td className="py-3 px-3 text-text-muted text-xs whitespace-nowrap">{(v.content_type || '').replace(/_/g, ' ')}</td>
                        <td className="py-3 px-3 text-right font-semibold tabular-nums">{fmtNum(v.views)}</td>
                        <td className="py-3 px-3 text-right tabular-nums">{fmtNum(v.likes)}</td>
                        <td className="py-3 px-3 text-right tabular-nums">{fmtNum(v.comments)}</td>
                        <td className="py-3 pl-3 text-right">
                          {v.youtube_url && <a href={v.youtube_url} target="_blank" rel="noreferrer" className="text-xs text-accent hover:underline whitespace-nowrap">YouTube ↗</a>}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
      </div>
    </div>
  )
}
