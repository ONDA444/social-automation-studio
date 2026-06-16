import { useEffect, useState } from 'react'
import { api, mediaUrl } from '../api'

// Aba dedicada aos Shorts verticais (TikTok / Reels / YouTube Shorts).
// Mostra cada short com player vertical, gancho do 1º frame e captions por
// plataforma já prontas para copiar e postar.
export default function Shorts() {
  const [jobs, setJobs] = useState([])
  const [loading, setLoading] = useState(true)

  const load = () => {
    setLoading(true)
    api.get('/jobs?limit=200')
      .then((d) => setJobs(Array.isArray(d) ? d : d?.jobs || []))
      .catch(() => setJobs([]))
      .finally(() => setLoading(false))
  }
  useEffect(load, [])

  // Build a flat list of shorts across jobs, preferring the rich shorts_meta.
  const cards = []
  for (const j of jobs) {
    const meta = j.video_context?.shorts_meta
    const list = (meta && meta.length)
      ? meta
      : (j.shorts_paths || []).map((p, i) => ({ path: p, num: i + 1, name: 'short' }))
    for (const s of list) cards.push({ job: j, short: s })
  }

  const copy = (txt) => { if (txt) navigator.clipboard?.writeText(txt) }

  return (
    <div className="space-y-5 fade-in">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <div>
            <div className="flex items-center gap-2">
              <h1 className="heading text-2xl text-gradient font-bold">Shorts</h1>
              {cards.length > 0 && <span className="badge" style={{ background: 'rgba(124,106,255,0.15)', color: 'var(--accent)' }}>{cards.length}</span>}
            </div>
            <p className="text-text-muted text-sm">Vídeos curtos verticais (TikTok • Reels • YouTube Shorts) — gancho + captions prontos.</p>
          </div>
        </div>
        <button className="btn-ghost" onClick={load}>↻ Atualizar</button>
      </div>

      {loading && (
        <div className="grid grid-cols-2 md:grid-cols-3 xl:grid-cols-4 gap-4">
          {[1,2,3,4].map(i => <div key={i} className="card skeleton aspect-[9/16]" />)}
        </div>
      )}

      {!loading && cards.length === 0 && (
        <div className="card p-12 text-center">
          <p className="text-5xl mb-4">📱</p>
          <p className="heading font-semibold text-lg">Nenhum Short ainda</p>
          <p className="text-text-muted text-sm mt-2">Crie um vídeo na Fila — os Shorts são gerados automaticamente em 9:16.</p>
        </div>
      )}

      <div className="grid grid-cols-2 md:grid-cols-3 xl:grid-cols-4 gap-4">
        {cards.map(({ job, short }, idx) => (
          <div key={`${job.id}-${short.num}-${idx}`} className="card p-3 flex flex-col gap-2">
            <div className="relative rounded-card overflow-hidden bg-black aspect-[9/16] group">
              {short.path
                ? <video src={mediaUrl(short.path)} controls preload="metadata" className="w-full h-full object-contain" />
                : <div className="grid place-items-center h-full text-text-muted text-xs">sem arquivo</div>}
              <div className="absolute bottom-0 inset-x-0 h-16 bg-gradient-to-t from-black/60 to-transparent" />
              {short.hook_overlay && (
                <div className="absolute top-2 inset-x-2 text-center">
                  <span className="inline-block bg-black/70 text-white text-[11px] font-bold px-2 py-1 rounded">
                    {short.hook_overlay}
                  </span>
                </div>
              )}
              {short.recommended && (
                <span className="absolute bottom-2 right-2 badge text-[10px]" style={{ background: 'var(--grad-accent)', color: '#fff' }}>★ recomendado</span>
              )}
            </div>

            <div className="text-xs">
              <div className="font-semibold truncate" title={job.title}>{job.title}</div>
              <div className="text-text-muted">
                #{job.id} · {short.name || 'short'}{short.length ? ` · ${Math.round(short.length)}s` : ''}
              </div>
            </div>

            {short.captions && (
              <div className="flex flex-wrap gap-1">
                {short.captions.tiktok && (
                  <button style={{ background: 'rgba(20,20,42,0.8)', color: 'var(--text-primary)' }} className="badge text-[10px] transition-colors hover:border-accent" title={short.captions.tiktok}
                          onClick={() => copy(short.captions.tiktok)}>📋 TikTok</button>
                )}
                {short.captions.instagram && (
                  <button style={{ background: 'rgba(20,20,42,0.8)', color: 'var(--text-primary)' }} className="badge text-[10px] transition-colors hover:border-accent" title={short.captions.instagram}
                          onClick={() => copy(short.captions.instagram)}>📋 Reels</button>
                )}
                {short.captions.youtube_shorts && (
                  <button style={{ background: 'rgba(20,20,42,0.8)', color: 'var(--text-primary)' }} className="badge text-[10px] transition-colors hover:border-accent" title={short.captions.youtube_shorts}
                          onClick={() => copy(short.captions.youtube_shorts)}>📋 Shorts</button>
                )}
              </div>
            )}
            {short.hashtags?.length > 0 && (
              <button className="text-[10px] text-text-muted text-left hover:text-accent truncate"
                      title={short.hashtags.join(' ')} onClick={() => copy(short.hashtags.join(' '))}>
                {short.hashtags.slice(0, 4).join(' ')}… <span className="opacity-70">(copiar)</span>
              </button>
            )}
          </div>
        ))}
      </div>
    </div>
  )
}
