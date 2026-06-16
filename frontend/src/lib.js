export const STATUS_META = {
  queued:                 { label: 'Na fila', color: 'var(--text-muted)' },
  processing:             { label: 'Processando', color: 'var(--accent)' },
  awaiting_approval:      { label: 'Aguardando aprovação', color: 'var(--warning)' },
  approved:               { label: 'Aprovado', color: 'var(--success)' },
  publishing:             { label: 'Publicando', color: 'var(--accent)' },
  published:              { label: 'Publicado', color: 'var(--success)' },
  rejected:               { label: 'Rejeitado', color: 'var(--error)' },
  error:                  { label: 'Erro', color: 'var(--error)' },
  tiktok_pending_approval:{ label: 'TikTok pendente', color: 'var(--warning)' },
}

export const PLATFORM_META = {
  youtube:   { label: 'YouTube', color: 'var(--accent-yt)', icon: '▶' },
  tiktok:    { label: 'TikTok', color: 'var(--accent-tk)', icon: '♪' },
  instagram: { label: 'Instagram', color: 'var(--accent-ig)', icon: '◎' },
}

// Idiomas suportados por canal. O valor (BCP-47) é gravado em content_language
// e o backend gera roteiro, título, descrição e voz nesse idioma.
export const LANGUAGES = [
  { code: 'pt-BR', label: '🇧🇷 Português' },
  { code: 'en-US', label: '🇺🇸 English' },
  { code: 'es-ES', label: '🇪🇸 Español' },
  { code: 'fr-FR', label: '🇫🇷 Français' },
  { code: 'de-DE', label: '🇩🇪 Deutsch' },
  { code: 'it-IT', label: '🇮🇹 Italiano' },
  { code: 'ja-JP', label: '🇯🇵 日本語' },
  { code: 'hi-IN', label: '🇮🇳 हिन्दी' },
  { code: 'ar-SA', label: '🇸🇦 العربية' },
]

export function statusMeta(s) {
  return STATUS_META[s] || { label: s, color: 'var(--text-muted)' }
}

export function fmtDate(iso) {
  if (!iso) return '—'
  try { return new Date(iso).toLocaleString('pt-BR', { dateStyle: 'short', timeStyle: 'short' }) }
  catch { return iso }
}

export function fmtNum(n) {
  if (n == null) return '0'
  if (n >= 1e6) return (n / 1e6).toFixed(1) + 'M'
  if (n >= 1e3) return (n / 1e3).toFixed(1) + 'k'
  return String(n)
}
