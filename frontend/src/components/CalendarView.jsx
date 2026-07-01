import { useMemo, useState } from 'react'
import { PLATFORM_META } from '../lib'

const WD = ['Dom', 'Seg', 'Ter', 'Qua', 'Qui', 'Sex', 'Sab']

export default function CalendarView({ events = [] }) {
  const [cursor, setCursor] = useState(() => {
    const d = new Date()
    return new Date(d.getFullYear(), d.getMonth(), 1)
  })

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

  const year = cursor.getFullYear()
  const month = cursor.getMonth()
  const firstDay = new Date(year, month, 1).getDay()
  const daysInMonth = new Date(year, month + 1, 0).getDate()
  const cells = []
  for (let i = 0; i < firstDay; i++) cells.push(null)
  for (let d = 1; d <= daysInMonth; d++) cells.push(new Date(year, month, d))

  const today = new Date().toDateString()

  return (
    <div className="card p-3 sm:p-5">
      <div className="flex items-center justify-between mb-4 gap-3">
        <div>
          <h3 className="heading font-extrabold">{cursor.toLocaleString('pt-BR', { month: 'long', year: 'numeric' })}</h3>
          <p className="text-xs text-text-muted mt-0.5">Publicacoes planejadas por dia e canal.</p>
        </div>
        <div className="flex gap-2">
          <button className="btn btn-ghost btn-sm" onClick={() => setCursor(new Date(year, month - 1, 1))} aria-label="Mes anterior">←</button>
          <button className="btn btn-ghost btn-sm" onClick={() => setCursor(new Date(year, month + 1, 1))} aria-label="Proximo mes">→</button>
        </div>
      </div>
      <div className="grid grid-cols-7 gap-1 text-center text-[11px] font-semibold text-text-muted mb-1.5">
        {WD.map((w) => <div key={w}>{w}</div>)}
      </div>
      <div className="grid grid-cols-7 gap-1">
        {cells.map((d, i) => {
          const dayEvents = d ? (byDay[d.toDateString()] || []) : []
          return (
            <div key={i} className={`min-h-[72px] sm:min-h-[92px] rounded-btn p-1.5 border ${d ? 'border-border bg-white' : 'border-transparent'} ${d && d.toDateString() === today ? 'ring-2 ring-accent/30' : ''}`}>
              {d && <span className="text-xs font-semibold text-text-muted">{d.getDate()}</span>}
              <div className="space-y-1 mt-1">
                {dayEvents.slice(0, 2).map((e) => {
                  const m = PLATFORM_META[(e.platforms || [])[0]] || { color: 'var(--accent)' }
                  const channel = e.channel_name || 'Sem canal'
                  return (
                    <div key={e.job_id} title={`${e.title} - ${channel}`} className="text-[10px] sm:text-[11px] rounded px-1.5 py-1 border" style={{ background: m.color + '10', borderColor: m.color + '24', color: m.color }}>
                      <div className="truncate font-semibold">{e.title}</div>
                      <div className="flex items-center gap-1 opacity-80">
                        <span className="inline-block w-1 h-1 rounded-full shrink-0" style={{ background: m.color }} />
                        <span className="truncate">{channel}</span>
                      </div>
                    </div>
                  )
                })}
                {dayEvents.length > 2 && (
                  <div className="text-[10px] text-text-muted px-1">+{dayEvents.length - 2} mais</div>
                )}
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}
