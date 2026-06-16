import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../api'
import { useWs } from '../App.jsx'
import VideoCard from '../components/VideoCard.jsx'
import AgentLog from '../components/AgentLog.jsx'
import AddJobModal from '../components/AddJobModal.jsx'
import { fmtNum } from '../lib'

// Accent colors per stat
const STAT_COLORS = [
  '#7C6AFF', // purple — jobs ativos
  '#F59E0B', // amber — aguardando
  '#00D68F', // green — publicados
  '#38BDF8', // sky — views
]

export default function Dashboard() {
  const [data, setData] = useState(null)
  const [trending, setTrending] = useState([])
  const [modal, setModal] = useState(false)
  const { count } = useWs()
  const nav = useNavigate()

  const load = () => api.get('/dashboard').then(setData).catch(() => setData({ error: true }))
  useEffect(() => {
    load()
    api.get('/dashboard/trending?niche=entretenimento')
      .then((d) => setTrending(d.suggestions || []))
      .catch(() => {})
  }, [])
  // Refresh job cards when pipeline events arrive.
  useEffect(() => { const t = setTimeout(load, 800); return () => clearTimeout(t) }, [count])

  /* ── Loading skeleton ── */
  if (!data) return (
    <div className="space-y-6">
      <div className="h-8 w-48 skeleton rounded-xl" />
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        {Array.from({ length: 4 }).map((_, i) => (
          <div key={i} className="card p-6"><div className="h-24 skeleton rounded-lg" /></div>
        ))}
      </div>
      <div className="grid grid-cols-2 md:grid-cols-3 gap-3">
        {Array.from({ length: 6 }).map((_, i) => <div key={i} className="h-44 skeleton rounded-xl" />)}
      </div>
    </div>
  )

  if (data.error) return (
    <div
      style={{
        padding: '24px',
        borderRadius: 16,
        background: 'rgba(255,71,87,0.08)',
        border: '1px solid rgba(255,71,87,0.25)',
        color: 'var(--error)',
        fontSize: 14,
      }}
    >
      Backend offline. Inicie a API em :8000.
    </div>
  )

  const sc = data.status_counts || {}
  const stats = [
    { label: 'Jobs ativos',           value: data.active_jobs || 0,     icon: '⚙️', color: STAT_COLORS[0] },
    { label: 'Aguardando aprovação',   value: sc.awaiting_approval || 0, icon: '✅', color: STAT_COLORS[1] },
    { label: 'Publicados',             value: sc.published || 0,         icon: '🚀', color: STAT_COLORS[2] },
    { label: 'Views totais',           value: fmtNum(data.total_views),  icon: '👁', color: STAT_COLORS[3] },
  ]

  /* Today's date — formatted in pt-BR */
  const today = new Date().toLocaleDateString('pt-BR', { weekday: 'long', year: 'numeric', month: 'long', day: 'numeric' })

  return (
    <div className="space-y-6">

      {/* ── Page header ── */}
      <div className="flex items-center justify-between">
        <div>
          <h2
            className="heading text-gradient font-black"
            style={{ fontSize: 28, lineHeight: 1.15, letterSpacing: '-0.5px' }}
          >
            Dashboard
          </h2>
          <p style={{ fontSize: 13, color: '#7070A0', marginTop: 4, textTransform: 'capitalize' }}>
            {today}
          </p>
        </div>
        <button
          className="btn-primary"
          onClick={() => setModal(true)}
          style={{
            background: 'linear-gradient(135deg, #7C6AFF 0%, #A78BFA 100%)',
            boxShadow: '0 4px 18px rgba(124,106,255,0.35)',
            border: 'none',
            padding: '10px 20px',
            borderRadius: 12,
            color: '#fff',
            fontWeight: 600,
            fontSize: 14,
            cursor: 'pointer',
            transition: 'box-shadow 180ms, transform 180ms',
          }}
          onMouseEnter={(e) => { e.currentTarget.style.boxShadow = '0 6px 24px rgba(124,106,255,0.5)'; e.currentTarget.style.transform = 'translateY(-1px)' }}
          onMouseLeave={(e) => { e.currentTarget.style.boxShadow = '0 4px 18px rgba(124,106,255,0.35)'; e.currentTarget.style.transform = 'translateY(0)' }}
        >
          + Novo vídeo
        </button>
      </div>

      {/* ── Stat cards ── */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        {stats.map((s) => (
          <div
            key={s.label}
            className="card-stat p-6 relative overflow-hidden"
            style={{
              background: 'rgba(14,14,26,0.9)',
              border: '1px solid rgba(255,255,255,0.07)',
              borderRadius: 16,
              backdropFilter: 'blur(16px)',
              transition: 'border-color 200ms, box-shadow 200ms',
              cursor: 'default',
            }}
            onMouseEnter={(e) => {
              e.currentTarget.style.borderColor = `${s.color}30`
              e.currentTarget.style.boxShadow = `0 0 24px ${s.color}15`
            }}
            onMouseLeave={(e) => {
              e.currentTarget.style.borderColor = 'rgba(255,255,255,0.07)'
              e.currentTarget.style.boxShadow = 'none'
            }}
          >
            {/* Watermark icon */}
            <span
              aria-hidden="true"
              className="select-none pointer-events-none"
              style={{
                position: 'absolute',
                right: 12,
                top: '50%',
                transform: 'translateY(-50%)',
                fontSize: 56,
                opacity: 0.09,
                lineHeight: 1,
              }}
            >
              {s.icon}
            </span>
            {/* Label */}
            <p style={{ fontSize: 12, color: '#7070A0', fontWeight: 500, margin: 0, position: 'relative', zIndex: 1 }}>
              {s.label}
            </p>
            {/* Value */}
            <p
              className="heading font-black tabular-nums"
              style={{
                fontSize: 36,
                lineHeight: 1.1,
                color: s.color,
                margin: '8px 0 0',
                position: 'relative',
                zIndex: 1,
                textShadow: `0 0 24px ${s.color}50`,
              }}
            >
              {s.value}
            </p>
          </div>
        ))}
      </div>

      {/* ── Recent videos + AgentLog ── */}
      <div className="grid grid-cols-1 xl:grid-cols-3 gap-6">
        <div className="xl:col-span-2 space-y-3">
          <h3
            className="heading font-bold"
            style={{ fontSize: 16, color: '#EEEEFF', letterSpacing: '-0.2px' }}
          >
            Vídeos recentes
          </h3>
          {(data.recent_jobs || []).length === 0 && (
            <p style={{ color: '#7070A0', fontSize: 14 }}>
              Nenhum vídeo ainda. Clique em "Novo vídeo".
            </p>
          )}
          <div className="grid grid-cols-2 md:grid-cols-3 gap-3">
            {(data.recent_jobs || []).map((j) => (
              <VideoCard key={j.id} job={j} onClick={() => nav('/queue')} />
            ))}
          </div>
        </div>

        {/* AgentLog wrapped in premium card */}
        <div
          className="h-[480px] flex flex-col"
          style={{
            background: 'rgba(14,14,26,0.9)',
            border: '1px solid rgba(124,106,255,0.18)',
            borderRadius: 16,
            overflow: 'hidden',
            boxShadow: '0 0 32px rgba(124,106,255,0.06)',
          }}
        >
          <div
            style={{
              padding: '14px 16px',
              borderBottom: '1px solid rgba(124,106,255,0.12)',
              display: 'flex',
              alignItems: 'center',
              gap: 8,
              flexShrink: 0,
            }}
          >
            <span
              style={{
                width: 8,
                height: 8,
                borderRadius: '50%',
                background: '#00D68F',
                boxShadow: '0 0 8px #00D68F',
                animation: 'sas-pulse 2.2s ease-in-out infinite',
                flexShrink: 0,
              }}
            />
            <span style={{ fontSize: 13, fontWeight: 600, color: '#EEEEFF' }}>Agente ao vivo</span>
          </div>
          <div style={{ flex: 1, overflow: 'hidden' }}>
            <AgentLog />
          </div>
        </div>
      </div>

      {/* ── Trending suggestions ── */}
      {trending.length > 0 && (
        <div
          style={{
            background: 'rgba(14,14,26,0.9)',
            border: '1px solid rgba(255,255,255,0.07)',
            borderRadius: 16,
            padding: '20px 22px',
          }}
        >
          <h3
            className="heading font-bold"
            style={{ fontSize: 15, color: '#EEEEFF', marginBottom: 14, display: 'flex', alignItems: 'center', gap: 8 }}
          >
            <span style={{ fontSize: 18 }}>💡</span>
            Sugestões do dia
          </h3>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
            {trending.slice(0, 8).map((t, i) => (
              <button
                key={i}
                onClick={() => setModal(true)}
                title={t.reason}
                style={{
                  padding: '6px 14px',
                  borderRadius: 20,
                  fontSize: 13,
                  fontWeight: 500,
                  background: 'rgba(124,106,255,0.1)',
                  border: '1px solid rgba(124,106,255,0.22)',
                  color: '#A78BFA',
                  cursor: 'pointer',
                  transition: 'background 150ms, border-color 150ms, color 150ms',
                }}
                onMouseEnter={(e) => {
                  e.currentTarget.style.background = 'rgba(124,106,255,0.25)'
                  e.currentTarget.style.borderColor = 'rgba(124,106,255,0.5)'
                  e.currentTarget.style.color = '#EEEEFF'
                }}
                onMouseLeave={(e) => {
                  e.currentTarget.style.background = 'rgba(124,106,255,0.1)'
                  e.currentTarget.style.borderColor = 'rgba(124,106,255,0.22)'
                  e.currentTarget.style.color = '#A78BFA'
                }}
              >
                {t.topic}
              </button>
            ))}
          </div>
        </div>
      )}

      <AddJobModal open={modal} onClose={() => setModal(false)} onCreated={load} />

      <style>{`
        @keyframes sas-pulse {
          0%, 100% { box-shadow: 0 0 5px #00D68F; opacity: 1; }
          50% { box-shadow: 0 0 13px #00D68F; opacity: 0.65; }
        }
      `}</style>
    </div>
  )
}
