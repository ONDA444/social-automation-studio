/**
 * Shared UI primitives — the "house style" lifted from the Analytics redesign so
 * every page looks the same: token-based, thin 1px borders, no blur/glow, count-up
 * numbers, honest section framing. Import these instead of re-styling inline.
 */
import { useEffect, useRef, useState } from 'react'
import { statusMeta } from '../lib'

const easeOut = (p) => 1 - Math.pow(1 - p, 3)

// Smoothly counts a number up to its new value on change. Respects reduced-motion.
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
    <div className="flex flex-wrap items-center justify-between gap-3">
      <div>
        <h2 className="heading text-2xl font-bold" style={{ color: 'var(--text-primary)' }}>{title}</h2>
        {sub && <p className="text-text-muted text-sm mt-0.5">{sub}</p>}
      </div>
      {children && <div className="flex items-center gap-2 flex-wrap">{children}</div>}
    </div>
  )
}

export function SectionCard({ title, sub, action, children, className = '' }) {
  return (
    <div className={`rounded-card p-5 ${className}`} style={{ background: 'var(--bg-surface)', border: '1px solid var(--border)' }}>
      {(title || action) && (
        <div className="flex items-center gap-3 mb-4">
          {title && <h3 className="heading font-semibold">{title}</h3>}
          {sub && <span className="text-text-muted text-xs">{sub}</span>}
          <div className="flex-1 h-px" style={{ background: 'rgba(255,255,255,0.06)' }} />
          {action}
        </div>
      )}
      {children}
    </div>
  )
}

// One KPI/stat tile with an animated value. Pass `format` (e.g. fmtNum) for big
// numbers, `accent` for the value color, and `onClick` to make it a shortcut.
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
          style={{ fontSize: 46, opacity: 0.08, lineHeight: 1 }}>{icon}</span>
      )}
      <p className="text-[11px] uppercase tracking-[0.08em]" style={{ color: 'var(--text-muted)' }}>{label}</p>
      <p className="heading font-black tabular-nums"
        style={{ fontSize: 'clamp(26px, 6vw, 34px)', lineHeight: 1.1, color: accent, marginTop: 6 }}>
        {display}
      </p>
    </Tag>
  )
}

// Status pill that reads its label+color from lib.js statusMeta — one source of
// truth for job status across Fila, Aprovações, Agenda, etc.
export function StatusBadge({ status, className = '' }) {
  const s = statusMeta(status)
  return (
    <span className={`badge shrink-0 ${className}`} style={{ background: s.color + '22', color: s.color }}>
      {s.label}
    </span>
  )
}

export function EmptyState({ icon = '∅', title, hint, action }) {
  return (
    <div className="flex flex-col items-center justify-center text-center py-10 px-4">
      <span className="text-3xl mb-2 opacity-50 select-none">{icon}</span>
      <p className="text-sm" style={{ color: 'var(--text-muted)' }}>{title}</p>
      {hint && <p className="text-xs mt-1" style={{ color: 'var(--text-dim)' }}>{hint}</p>}
      {action && <div className="mt-3">{action}</div>}
    </div>
  )
}
