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

// Free edge-tts preset voices (no cloning, $0) — each channel can pick a distinct
// one so channels don't all sound the same. Grouped by language; the picker shows
// the voices matching the channel's language. (M)=masculino (F)=feminino.
export const FREE_VOICES = [
  { id: 'pt-BR-AntonioNeural', lang: 'pt', label: 'Antônio (M)' },
  { id: 'pt-BR-FabioNeural', lang: 'pt', label: 'Fábio (M)' },
  { id: 'pt-BR-DonatoNeural', lang: 'pt', label: 'Donato (M)' },
  { id: 'pt-BR-HumbertoNeural', lang: 'pt', label: 'Humberto (M)' },
  { id: 'pt-BR-NicolauNeural', lang: 'pt', label: 'Nicolau (M)' },
  { id: 'pt-BR-FranciscaNeural', lang: 'pt', label: 'Francisca (F)' },
  { id: 'pt-BR-BrendaNeural', lang: 'pt', label: 'Brenda (F)' },
  { id: 'pt-BR-ThalitaNeural', lang: 'pt', label: 'Thalita (F)' },
  { id: 'pt-BR-LeilaNeural', lang: 'pt', label: 'Leila (F)' },
  { id: 'pt-BR-YaraNeural', lang: 'pt', label: 'Yara (F)' },
  { id: 'en-US-GuyNeural', lang: 'en', label: 'Guy (M)' },
  { id: 'en-US-ChristopherNeural', lang: 'en', label: 'Christopher (M)' },
  { id: 'en-US-EricNeural', lang: 'en', label: 'Eric (M)' },
  { id: 'en-US-JennyNeural', lang: 'en', label: 'Jenny (F)' },
  { id: 'en-US-AriaNeural', lang: 'en', label: 'Aria (F)' },
  { id: 'en-US-MichelleNeural', lang: 'en', label: 'Michelle (F)' },
  { id: 'es-ES-AlvaroNeural', lang: 'es', label: 'Álvaro (M)' },
  { id: 'es-ES-ElviraNeural', lang: 'es', label: 'Elvira (F)' },
  { id: 'fr-FR-HenriNeural', lang: 'fr', label: 'Henri (M)' },
  { id: 'fr-FR-DeniseNeural', lang: 'fr', label: 'Denise (F)' },
  { id: 'de-DE-ConradNeural', lang: 'de', label: 'Conrad (M)' },
  { id: 'de-DE-KatjaNeural', lang: 'de', label: 'Katja (F)' },
  { id: 'it-IT-DiegoNeural', lang: 'it', label: 'Diego (M)' },
  { id: 'it-IT-ElsaNeural', lang: 'it', label: 'Elsa (F)' },
  { id: 'ja-JP-KeitaNeural', lang: 'ja', label: 'Keita (M)' },
  { id: 'ja-JP-NanamiNeural', lang: 'ja', label: 'Nanami (F)' },
  { id: 'hi-IN-MadhurNeural', lang: 'hi', label: 'Madhur (M)' },
  { id: 'hi-IN-SwaraNeural', lang: 'hi', label: 'Swara (F)' },
  { id: 'ar-SA-HamedNeural', lang: 'ar', label: 'Hamed (M)' },
  { id: 'ar-SA-ZariyahNeural', lang: 'ar', label: 'Zariyah (F)' },
]

// Voices available for a channel's language (falls back to all if none match).
export function voicesForLang(language) {
  const p = (language || 'pt-BR').toLowerCase().split('-')[0]
  const m = FREE_VOICES.filter((v) => v.lang === p)
  return m.length ? m : FREE_VOICES
}

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
