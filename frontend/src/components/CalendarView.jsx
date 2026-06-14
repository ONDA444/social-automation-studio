import { useMemo, useState } from 'react'
import { PLATFORM_META } from '../lib'

const WD = ['Dom', 'Seg', 'Ter', 'Qua', 'Qui', 'Sex', 'Sáb']

export default function CalendarView({ events = [] }) {
  const [cursor, setCursor] = useState(() => { const d = new Date(); return new Date(d.getFullYear(), d.getMonth(), 1) })

  const byDay = useMemo(() => {
    const map = {}
    for (const e of events) {
      if (!e.scheduled_at) continue
      const d = new Date(e.scheduled_at)
      const key = d.toDateString()
      ;(map[key] ||= []).push(e)
    }
    return map
  }, [events])

  const year = cursor.getFullYear(), month = cursor.getMonth()
  const firstDay = new Date(year, month, 1).getDay()
  const daysInMonth = new Date(year, month + 1, 0).getDate()
  const cells = []
  for (let i = 0; i < firstDay; i++) cells.push(null)
  for (let d = 1; d <= daysInMonth; d++) cells.push(new Date(year, month, d))

  const today = new Date().toDateString()

  return (
    <div className="card p-5">
      <div className="flex items-center justify-between mb-4">
        <h3 className="heading font-semibold">{cursor.toLocaleString('pt-BR', { month: 'long', year: 'numeric' })}</h3>
        <div className="flex gap-2">
          <button className="btn-ghost text-xs" onClick={() => setCursor(new Date(year, month - 1, 1))}>←</button>
          <button className="btn-ghost text-xs" onClick={() => setCursor(new Date(year, month + 1, 1))}>→</button>
        </div>
      </div>
      <div className="grid grid-cols-7 gap-1 text-center text-[11px] text-text-muted mb-1">
        {WD.map((w) => <div key={w}>{w}</div>)}
      </div>
      <div className="grid grid-cols-7 gap-1">
        {cells.map((d, i) => (
          <div key={i} className={`min-h-[68px] rounded-btn p-1 border ${d ? 'border-border bg-elevated/40' : 'border-transparent'} ${d && d.toDateString() === today ? 'ring-1 ring-accent' : ''}`}>
            {d && <span className="text-[11px] text-text-muted">{d.getDate()}</span>}
            <div className="space-y-0.5 mt-0.5">
              {d && (byDay[d.toDateString()] || []).slice(0, 3).map((e) => {
                const m = PLATFORM_META[(e.platforms || [])[0]] || { color: 'var(--accent)' }
                return <div key={e.job_id} title={e.title} className="text-[9px] truncate rounded px-1 py-0.5" style={{ background: m.color + '22', color: m.color }}>{e.title}</div>
              })}
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}
