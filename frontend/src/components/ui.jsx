import { useEffect, useRef, useState } from 'react'
import { statusMeta } from '../lib'

const easeOut = (p) => 1 - Math.pow(1 - p, 3)

export function useCountUp(target, ms = 650) {
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

export function PageHeader({ title, sub, children }) {
  return (
    <div className="flex flex-wrap items-start justify-between gap-3">
      <div>
        <h2 className="heading text-2xl sm:text-3xl font-extrabold" style={{ color: 'var(--text-primary)' }}>{title}</h2>
        {sub && <p className="text-text-muted text-sm mt-1 max-w-2xl">{sub}</p>}
      </div>
      {children && <div className="flex items-center gap-2 flex-wrap">{children}</div>}
    </div>
  )
}

export function SectionCard({ title, sub, action, children, className = '' }) {
  return (
    <div className={`rounded-card p-5 ${className}`} style={{ background: 'var(--bg-surface)', border: '1px solid var(--border)', boxShadow: 'var(--shadow-card)' }}>
      {(title || action) && (
        <div className="flex items-center gap-3 mb-4">
          {title && <h3 className="heading font-extrabold">{title}</h3>}
          {sub && <span className="text-text-muted text-xs">{sub}</span>}
          <div className="flex-1 h-px" style={{ background: 'var(--border)' }} />
          {action}
        </div>
      )}
      {children}
    </div>
  )
}

export function StatTile({ label, value, format, accent = 'var(--text-primary)', icon, onClick }) {
  const numeric = typeof value === 'number'
  const shown = useCountUp(numeric ? value : 0)
  const display = numeric ? (format ? format(shown) : shown) : value
  const Tag = onClick ? 'button' : 'div'
  return (
    <Tag
      type={onClick ? 'button' : undefined}
      onClick={onClick}
      className="card-stat p-4 sm:p-5 relative overflow-hidden text-left w-full"
      style={{ cursor: onClick ? 'pointer' : 'default' }}
    >
      {icon && (
        <span aria-hidden className="absolute right-3 top-1/2 -translate-y-1/2 select-none pointer-events-none"
          style={{ fontSize: 42, opacity: 0.10, lineHeight: 1, color: accent }}>{icon}</span>
      )}
      <p className="text-[11px] uppercase tracking-[0.08em]" style={{ color: 'var(--text-muted)' }}>{label}</p>
      <p className="heading font-black tabular-nums" style={{ fontSize: 32, lineHeight: 1.05, color: accent, marginTop: 8 }}>
        {display}
      </p>
    </Tag>
  )
}

export function StatusBadge({ status, className = '' }) {
  const s = statusMeta(status)
  return (
    <span className={`badge shrink-0 ${className}`} style={{ background: s.color + '18', color: s.color, borderColor: s.color + '30' }}>
      {s.label}
    </span>
  )
}

export function EmptyState({ icon = '-', title, hint, action }) {
  return (
    <div className="flex flex-col items-center justify-center text-center py-12 px-4">
      <span className="w-11 h-11 rounded-card mb-3 flex items-center justify-center border select-none"
        style={{ background: 'var(--bg-elevated)', borderColor: 'var(--border)', color: 'var(--text-muted)' }}>
        {icon}
      </span>
      <p className="text-sm font-semibold" style={{ color: 'var(--text-primary)' }}>{title}</p>
      {hint && <p className="text-xs mt-1 max-w-sm" style={{ color: 'var(--text-muted)' }}>{hint}</p>}
      {action && <div className="mt-4">{action}</div>}
    </div>
  )
}
