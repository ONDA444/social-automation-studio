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
    <div className="space-y-5">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="heading text-2xl font-bold">Shorts</h1>
          <p className="text-text-muted text-sm">Vídeos curtos verticais (TikTok • Reels • YouTube Shorts) — gancho + captions prontos.</p>
        </div>
        <button className="btn-ghost" onClick={load}>↻ Atualizar</button>
      </div>

      {loading && <p className="text-text-muted">Carregando…</p>}
      {!loading && cards.length === 0 && (
        <div className="card p-8 text-center text-text-muted">
          Nenhum Short ainda. Gere um vídeo na <b>Fila</b> — cada vídeo produz Shorts automaticamente.
        </div>
      )}

      <div className="grid grid-cols-2 md:grid-cols-3 xl:grid-cols-4 gap-4">
        {cards.map(({ job, short }, idx) => (
          <div key={`${job.id}-${short.num}-${idx}`} className="card p-3 flex flex-col gap-2">
            <div className="relative rounded-card overflow-hidden bg-black aspect-[9/16]">
              {short.path
                ? <video src={mediaUrl(short.path)} controls preload="metadata" className="w-full h-full object-contain" />
                : <div className="grid place-items-center h-full text-text-muted text-xs">sem arquivo</div>}
              {short.hook_overlay && (
                <div className="absolute top-2 inset-x-2 text-center">
                  <span className="inline-block bg-black/70 text-white text-[11px] font-bold px-2 py-1 rounded">
                    {short.hook_overlay}
                  </span>
                </div>
              )}
              {short.recommended && (
                <span className="absolute bottom-2 right-2 badge bg-accent text-white text-[10px]">★ recomendado</span>
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
                  <button className="badge bg-elevated hover:bg-accent/20 text-[10px]" title={short.captions.tiktok}
                          onClick={() => copy(short.captions.tiktok)}>📋 TikTok</button>
                )}
                {short.captions.instagram && (
                  <button className="badge bg-elevated hover:bg-accent/20 text-[10px]" title={short.captions.instagram}
                          onClick={() => copy(short.captions.instagram)}>📋 Reels</button>
                )}
                {short.captions.youtube_shorts && (
                  <button className="badge bg-elevated hover:bg-accent/20 text-[10px]" title={short.captions.youtube_shorts}
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
