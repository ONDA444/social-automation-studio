import { mediaUrl } from '../api'
import { statusMeta, PLATFORM_META, fmtDate } from '../lib'

export default function VideoCard({ job, onClick }) {
  const s = statusMeta(job.status)
  const thumb = mediaUrl(job.thumbnail_path)
  return (
    <div className="card p-3 hover:border-accent/50 transition-colors cursor-pointer" onClick={onClick}>
      <div className="aspect-video rounded-btn overflow-hidden bg-elevated mb-3 flex items-center justify-center">
        {thumb ? (
          <img src={thumb} alt="" className="w-full h-full object-cover" onError={(e) => (e.target.style.display = 'none')} />
        ) : (
          <span className="text-3xl opacity-40">🎬</span>
        )}
      </div>
      <div className="flex items-start justify-between gap-2">
        <h3 className="text-sm font-medium leading-snug line-clamp-2">{job.title}</h3>
        <span className="badge shrink-0" style={{ background: s.color + '22', color: s.color }}>{s.label}</span>
      </div>
      {job.status === 'processing' && (
        <div className="mt-2">
          <div className="h-1.5 rounded-full bg-elevated overflow-hidden">
            <div className="h-full bg-accent transition-all" style={{ width: `${job.progress || 0}%` }} />
          </div>
          <p className="text-[11px] text-text-muted mt-1">{job.current_agent || '...'} · {job.progress}%</p>
        </div>
      )}
      <div className="flex items-center gap-1.5 mt-2">
        {(job.target_platforms || []).map((p) => {
          const m = PLATFORM_META[p]
          return m ? (
            <span key={p} className="badge text-[10px]" style={{ background: m.color + '22', color: m.color }}>
              {m.icon} {m.label}
            </span>
          ) : null
        })}
        <span className="text-[10px] text-text-muted ml-auto">{fmtDate(job.created_at)}</span>
      </div>
    </div>
  )
}
