import { useCallback, useEffect, useState } from 'react'
import { api, mediaUrl } from '../api'
import { PageHeader, SectionCard, EmptyState, ErrorBanner } from '../components/ui.jsx'
import { fmtDate } from '../lib'

// Página Mídia (§25) — biblioteca real de tudo que o pipeline já gerou:
// vídeos principais, shorts e thumbnails. Só lista arquivos que EXISTEM no
// disco (o backend mede o tamanho via stat); player e preview usam o endpoint
// /media de streaming do backend.

const KIND_LABEL = { video: 'Vídeo', short: 'Short', thumb: 'Thumbnail' }
const KIND_ICON = { video: '🎬', short: '📱', thumb: '🖼' }
const FILTERS = [
  { key: 'all', label: 'Tudo' },
  { key: 'video', label: 'Vídeos' },
  { key: 'short', label: 'Shorts' },
  { key: 'thumb', label: 'Thumbs' },
]

function MediaCard({ item }) {
  const [playing, setPlaying] = useState(false)
  const url = mediaUrl(item.path)
  return (
    <div className="rounded-card overflow-hidden fade-in card-hover"
      style={{ background: 'var(--bg-surface)', border: '1px solid var(--border)' }}>
      <div className="relative" style={{ aspectRatio: item.kind === 'short' ? '9/16' : '16/9', background: 'var(--bg-elevated)' }}>
        {item.kind === 'thumb' ? (
          <img src={url} alt={item.title} className="w-full h-full object-cover" loading="lazy" />
        ) : playing ? (
          <video src={url} controls autoPlay className="w-full h-full object-contain" />
        ) : (
          <button
            className="w-full h-full grid place-items-center cursor-pointer group"
            onClick={() => setPlaying(true)}
            title="Reproduzir"
            style={{ background: 'transparent' }}
          >
            <span className="text-3xl opacity-70">{KIND_ICON[item.kind]}</span>
            <span className="absolute bottom-2 right-2 w-8 h-8 rounded-full grid place-items-center transition-transform group-hover:scale-110"
              style={{ background: 'var(--accent)', color: 'var(--text-inverse)' }}>▶</span>
          </button>
        )}
      </div>
      <div className="p-3">
        <p className="text-xs font-bold truncate" style={{ color: 'var(--text-primary)' }} title={item.title}>
          {item.title || `Job #${item.job_id}`}
        </p>
        <p className="text-[10px] mt-1 font-mono" style={{ color: 'var(--text-dim)' }}>
          {item.size_mb?.toFixed(1)} MB · {fmtDate(item.updated_at)}
        </p>
        <div className="flex items-center gap-1.5 mt-2">
          <span className="badge text-[9px]">{KIND_LABEL[item.kind]}</span>
          <span className="badge text-[9px]">{item.status}</span>
        </div>
      </div>
    </div>
  )
}

export default function Media() {
  const [kind, setKind] = useState('all')
  const [data, setData] = useState(null)
  const [loadError, setLoadError] = useState(false)

  const load = useCallback(() => api.get(`/media/library?kind=${kind}`)
    .then((d) => { setData(d); setLoadError(false) })
    .catch(() => setLoadError(true)), [kind])

  useEffect(() => { load() }, [load])

  return (
    <div className="space-y-4">
      <PageHeader
        title="Biblioteca de mídia"
        sub={data ? `${data.count} arquivo${data.count === 1 ? '' : 's'} · ${data.total_mb} MB gerados` : 'tudo que o sistema produziu'}
      />
      <div className="flex gap-2 flex-wrap">
        {FILTERS.map((f) => (
          <button
            key={f.key}
            className={kind === f.key ? 'btn-primary btn-sm' : 'btn-ghost btn-sm'}
            onClick={() => setKind(f.key)}
          >
            {f.label}
          </button>
        ))}
      </div>
      {loadError && <ErrorBanner message="Não foi possível carregar a biblioteca." onRetry={load} />}
      <SectionCard>
        {!data && !loadError ? (
          <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 gap-3">
            {Array.from({ length: 8 }).map((_, i) => <div key={i} className="skeleton h-44 rounded-card" />)}
          </div>
        ) : data?.count === 0 ? (
          <EmptyState
            icon="🎬"
            title="Nenhuma mídia ainda"
            hint="Quando o pipeline terminar um vídeo, ele aparece aqui automaticamente."
          />
        ) : (
          <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 gap-3 stagger">
            {data?.items.map((item, i) => <MediaCard key={`${item.kind}-${item.job_id}-${i}`} item={item} />)}
          </div>
        )}
      </SectionCard>
    </div>
  )
}
