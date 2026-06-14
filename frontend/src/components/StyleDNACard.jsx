export default function StyleDNACard({ dna }) {
  if (!dna) return null
  const vs = dna.visual_style || {}
  const audio = dna.audio || {}
  const pacing = dna.pacing || {}
  return (
    <div className="card p-5 space-y-4">
      <div className="flex items-center justify-between">
        <h3 className="heading font-semibold">StyleDNA</h3>
        <span className="badge bg-accent/20 text-accent">{dna.content_type}</span>
      </div>

      <div className="flex gap-1.5">
        {(vs.dominant_colors || []).map((c, i) => (
          <div key={i} className="w-8 h-8 rounded-btn border border-border" style={{ background: c }} title={c} />
        ))}
      </div>

      <div className="grid grid-cols-2 gap-3 text-sm">
        <Field label="Proporção" value={dna.aspect_ratio} />
        <Field label="Resolução" value={dna.resolution} />
        <Field label="FPS" value={dna.fps} />
        <Field label="Duração" value={dna.duration ? `${dna.duration}s` : '—'} />
        <Field label="Grading" value={vs.grading_style} />
        <Field label="Brilho" value={vs.brightness} />
        <Field label="Ritmo" value={pacing.style} />
        <Field label="Clipe médio" value={pacing.avg_clip_duration ? `${pacing.avg_clip_duration}s` : '—'} />
        <Field label="Narração" value={audio.has_narration ? 'sim' : 'não'} />
        <Field label="Música" value={audio.music_mood || (audio.has_music ? 'sim' : 'não')} />
      </div>

      {dna._note && <p className="text-xs text-warning">{dna._note}</p>}
    </div>
  )
}

function Field({ label, value }) {
  return (
    <div>
      <p className="text-[11px] text-text-muted uppercase tracking-wide">{label}</p>
      <p className="font-medium">{value ?? '—'}</p>
    </div>
  )
}
