import { createContext, useCallback, useContext, useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { statusMeta } from '../lib'

const easeOut = (p) => 1 - Math.pow(1 - p, 3)

const FOCUSABLE_SELECTOR = 'a[href], button:not([disabled]), textarea:not([disabled]), input:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])'

// Shared modal shell: portal + backdrop + focus trap + Esc-to-close.
// All 4 modals in the app used to hand-roll this exact same block.
//
// Portal to document.body: a page wrapper's `.fade-in` animation leaves a
// residual `transform` after finishing (animation-fill-mode: both), which
// breaks `fixed inset-0` positioning for any modal nested inside it (the
// ancestor's transform creates a new containing block, pinning `fixed` to
// that box instead of the viewport). A portal escapes that ancestor entirely.
export function Modal({
  open = true,
  onClose,
  labelledBy,
  children,
  closeOnBackdrop = true,
  overlayClassName = 'fixed inset-0 bg-black/60 backdrop-blur-sm grid place-items-center z-50 p-3 sm:p-4 fade-in overflow-y-auto',
  overlayStyle,
  cardClassName = 'card p-4 sm:p-6 w-full max-w-lg max-h-[90vh] overflow-y-auto shadow-[0_20px_60px_rgba(0,0,0,0.5)] animate-[fadeIn_.2s_ease-out] origin-center',
  cardStyle,
}) {
  const cardRef = useRef(null)

  useEffect(() => {
    if (!open) return
    const card = cardRef.current
    const previouslyFocused = document.activeElement
    if (card && !card.contains(document.activeElement)) {
      (card.querySelector(FOCUSABLE_SELECTOR) || card).focus()
    }
    const onKeyDown = (e) => {
      if (e.key === 'Escape') { onClose?.(); return }
      if (e.key !== 'Tab' || !card) return
      const items = Array.from(card.querySelectorAll(FOCUSABLE_SELECTOR))
      if (!items.length) return
      const idx = items.indexOf(document.activeElement)
      if (e.shiftKey) {
        if (idx <= 0) { e.preventDefault(); items[items.length - 1].focus() }
      } else if (idx === -1 || idx === items.length - 1) {
        e.preventDefault(); items[0].focus()
      }
    }
    document.addEventListener('keydown', onKeyDown)
    return () => {
      document.removeEventListener('keydown', onKeyDown)
      if (previouslyFocused instanceof HTMLElement) previouslyFocused.focus()
    }
  }, [open, onClose])

  if (!open) return null
  return createPortal(
    <div className={overlayClassName} style={overlayStyle} onClick={closeOnBackdrop ? onClose : undefined}>
      <div ref={cardRef} role="dialog" aria-modal="true" aria-labelledby={labelledBy} tabIndex={-1}
        className={cardClassName} style={cardStyle} onClick={(e) => e.stopPropagation()}>
        {children}
      </div>
    </div>,
    document.body
  )
}

const TOAST_META = {
  success: { icon: '✓', color: 'var(--success)' },
  error:   { icon: '✕', color: 'var(--error)' },
  info:    { icon: 'ℹ', color: 'var(--accent-blue)' },
}
let toastSeq = 0
const ToastContext = createContext(null)

// App-wide toast host — replaces alert()-as-feedback with a themeable,
// non-blocking notification. Mount once near the root (see App.jsx); call
// useToast() anywhere below it in the tree.
export function ToastProvider({ children }) {
  const [toasts, setToasts] = useState([])

  const dismiss = useCallback((id) => {
    setToasts((prev) => prev.filter((t) => t.id !== id))
  }, [])

  const push = useCallback((message, type, opts = {}) => {
    const id = ++toastSeq
    setToasts((prev) => [...prev, { id, message, type }])
    const duration = opts.duration ?? (type === 'error' ? 7000 : 4500)
    if (duration > 0) setTimeout(() => dismiss(id), duration)
    return id
  }, [dismiss])

  const toast = useRef({
    success: (message, opts) => push(message, 'success', opts),
    error: (message, opts) => push(message, 'error', opts),
    info: (message, opts) => push(message, 'info', opts),
    dismiss,
  }).current

  return (
    <ToastContext.Provider value={toast}>
      {children}
      {toasts.length > 0 && createPortal(
        <div className="fixed z-[70] bottom-4 right-4 left-4 sm:left-auto flex flex-col items-stretch sm:items-end gap-2 pointer-events-none">
          {toasts.map((t) => (
            <ToastItem key={t.id} toast={t} onDismiss={() => dismiss(t.id)} />
          ))}
        </div>,
        document.body
      )}
    </ToastContext.Provider>
  )
}

export function useToast() {
  const ctx = useContext(ToastContext)
  if (!ctx) throw new Error('useToast must be used inside <ToastProvider>')
  return ctx
}

function ToastItem({ toast, onDismiss }) {
  const meta = TOAST_META[toast.type] || TOAST_META.info
  return (
    <div
      role={toast.type === 'error' ? 'alert' : 'status'}
      aria-live={toast.type === 'error' ? 'assertive' : 'polite'}
      className="card pointer-events-auto slide-down flex items-start gap-2.5 p-3 pr-2.5 sm:min-w-[280px] sm:max-w-sm"
      style={{ borderLeft: `3px solid ${meta.color}` }}
    >
      <span aria-hidden className="shrink-0 w-5 h-5 rounded-full grid place-items-center text-[11px] font-bold"
        style={{ background: `color-mix(in srgb, ${meta.color} 18%, transparent)`, color: meta.color }}>
        {meta.icon}
      </span>
      <p className="flex-1 text-sm leading-snug" style={{ color: 'var(--text-primary)', whiteSpace: 'pre-line' }}>{toast.message}</p>
      <button onClick={onDismiss} className="shrink-0 p-1 leading-none text-text-muted hover:text-text-primary transition-colors" aria-label="Fechar notificação">✕</button>
    </div>
  )
}

const ConfirmContext = createContext(null)

// Promise-based replacement for window.confirm(): `await confirm('...')`
// resolves to true/false, but renders as a themed Modal instead of a
// blocking native dialog. opts: { title, confirmLabel, cancelLabel, danger }.
export function ConfirmProvider({ children }) {
  const [state, setState] = useState(null)

  const confirm = useCallback((message, opts = {}) => (
    new Promise((resolve) => setState({ message, opts, resolve }))
  ), [])

  const close = (result) => {
    state?.resolve(result)
    setState(null)
  }

  return (
    <ConfirmContext.Provider value={confirm}>
      {children}
      <Modal
        open={!!state}
        onClose={() => close(false)}
        labelledBy="confirm-dialog-title"
        cardClassName="card p-5 w-full max-w-sm shadow-[0_20px_60px_rgba(0,0,0,0.5)] animate-[fadeIn_.2s_ease-out] origin-center"
      >
        {state && (
          <>
            <h3 id="confirm-dialog-title" className="heading text-base font-bold mb-2">{state.opts.title || 'Confirmar ação'}</h3>
            <p className="text-sm" style={{ color: 'var(--text-muted)', whiteSpace: 'pre-line' }}>{state.message}</p>
            <div className="flex flex-col-reverse sm:flex-row gap-2 sm:justify-end mt-5">
              <button className="btn-ghost w-full sm:w-auto" onClick={() => close(false)}>{state.opts.cancelLabel || 'Cancelar'}</button>
              <button className={`w-full sm:w-auto ${state.opts.danger ? 'btn-danger' : 'btn-primary'}`} autoFocus onClick={() => close(true)}>
                {state.opts.confirmLabel || 'Confirmar'}
              </button>
            </div>
          </>
        )}
      </Modal>
    </ConfirmContext.Provider>
  )
}

export function useConfirm() {
  const ctx = useContext(ConfirmContext)
  if (!ctx) throw new Error('useConfirm must be used inside <ConfirmProvider>')
  return ctx
}

// Guards against stale async responses: a slow request from a previous selection
// (e.g. an account switch) must not overwrite state set by a newer one. Call start()
// right before firing the request, then check isCurrent(id) in each .then()/after each
// await before committing state.
export function useLatestRequest() {
  const ref = useRef(0)
  const start = useCallback(() => ++ref.current, [])
  const isCurrent = useCallback((id) => id === ref.current, [])
  return { start, isCurrent }
}

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
    <span className={`badge shrink-0 ${className}`} style={{ background: `color-mix(in srgb, ${s.color} 18%, transparent)`, color: s.color, borderColor: `color-mix(in srgb, ${s.color} 30%, transparent)` }}>
      {s.label}
    </span>
  )
}

// Distinguishes "the backend is unreachable" from "there's just no data yet" —
// pair with a fetch's .catch() so a dead API doesn't silently render as an
// empty list. Keeps whatever data was last loaded successfully on screen.
export function ErrorBanner({ message = 'Não foi possível falar com o servidor.', onRetry, className = '' }) {
  return (
    <div className={`rounded-card px-4 py-2.5 text-sm flex items-center gap-3 flex-wrap ${className}`}
      style={{ background: 'rgba(255,80,102,0.08)', border: '1px solid rgba(255,80,102,0.25)', color: 'var(--error)' }}>
      <span>⚠️ {message}</span>
      {onRetry && (
        <button type="button" className="btn-ghost btn-sm ml-auto" style={{ color: 'var(--error)', borderColor: 'rgba(255,80,102,0.3)' }} onClick={onRetry}>
          ↻ Tentar novamente
        </button>
      )}
    </div>
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
