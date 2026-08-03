export const STATUS_META = {
  queued:                 { label: 'Na fila', color: 'var(--text-muted)' },
  processing:             { label: 'Processando', color: 'var(--accent-blue)' },
  awaiting_approval:      { label: 'Aguardando aprovacao', color: 'var(--warning)' },
  approved:               { label: 'Aprovado', color: 'var(--success)' },
  publishing:             { label: 'Publicando', color: 'var(--accent-blue)' },
  published:              { label: 'Publicado', color: 'var(--success)' },
  rejected:               { label: 'Rejeitado', color: 'var(--error)' },
  error:                  { label: 'Erro', color: 'var(--error)' },
  tiktok_pending_approval:{ label: 'TikTok pendente', color: 'var(--warning)' },
  awaiting_quota:         { label: 'Aguardando quota', color: 'var(--warning)' },
}

export const PLATFORM_META = {
  youtube:   { label: 'YouTube', color: 'var(--accent-yt)', icon: 'YT' },
  tiktok:    { label: 'TikTok', color: 'var(--accent-tk)', icon: 'TT' },
  instagram: { label: 'Instagram', color: 'var(--accent-ig)', icon: 'IG' },
}

// Mirrors backend/intro_modes.py's INTRO_MODES tuple (single source of truth
// there; no endpoint exposes it, so this is kept in sync by hand like
// Schedule.jsx's MODE_OPTIONS/FALLBACK_CONTENT_TYPES).
export const INTRO_MODE_OPTIONS = [
  { value: 'mixed', label: 'Misto (recomendado)', hint: 'Alterna entre os modos abaixo automaticamente.' },
  { value: 'tts', label: 'TTS', hint: 'Sempre voz sintetica.' },
  { value: 'voice_bank', label: 'Banco de vozes', hint: 'Usa gravacoes do operador quando disponiveis.' },
  { value: 'text_only', label: 'Somente texto', hint: 'Sem narracao no intro/overlay.' },
]

// Fallback usado quando GET /jobs/content-types falhar ou ainda não existir.
// Espelha backend/content_types.py (CONTENT_TYPES) — única fonte compartilhada
// pelas telas que listam tipos de conteúdo, pra não divergirem entre si.
export const FALLBACK_CONTENT_TYPES = [
  { value: 'auto', label: '✨ Automático (IA detecta)' },
  { value: 'film_recap_ai_images', label: 'Recap de Filme (imagens IA)' },
  { value: 'sports_highlights', label: 'Melhores Momentos (Esportes)' },
  { value: 'quote_viral', label: 'Frase Viral' },
  { value: 'top_list_ranking', label: 'Top 5/10 (Ranking)' },
  { value: 'explainer_curiosity', label: 'Curiosidade Explicada' },
  { value: 'true_crime_mystery', label: 'True Crime / Mistério' },
  { value: 'reaction_commentary', label: 'Reação / Comentário' },
  { value: 'reddit_story', label: 'História do Reddit' },
  { value: 'motivational_speech', label: 'Discurso Motivacional' },
]

export const LANGUAGES = [
  { code: 'pt-BR', label: 'Portugues' },
  { code: 'en-US', label: 'English' },
  { code: 'es-ES', label: 'Espanol' },
  { code: 'fr-FR', label: 'Francais' },
  { code: 'de-DE', label: 'Deutsch' },
  { code: 'it-IT', label: 'Italiano' },
  { code: 'ja-JP', label: 'Japanese' },
  { code: 'hi-IN', label: 'Hindi' },
  { code: 'ar-SA', label: 'Arabic' },
]

export const FREE_VOICES = [
  { id: 'pt-BR-AntonioNeural', lang: 'pt', label: 'Antonio (M)' },
  { id: 'pt-BR-FabioNeural', lang: 'pt', label: 'Fabio (M)' },
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
  { id: 'es-ES-AlvaroNeural', lang: 'es', label: 'Alvaro (M)' },
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

export function voicesForLang(language) {
  const p = (language || 'pt-BR').toLowerCase().split('-')[0]
  const m = FREE_VOICES.filter((v) => v.lang === p)
  return m.length ? m : FREE_VOICES
}

export function statusMeta(s) {
  return STATUS_META[s] || { label: s, color: 'var(--text-muted)' }
}

export function fmtDate(iso) {
  if (!iso) return '-'
  try { return new Date(iso).toLocaleString('pt-BR', { dateStyle: 'short', timeStyle: 'short' }) }
  catch { return iso }
}

export function fmtNum(n) {
  if (n == null) return '0'
  if (n >= 1e6) return (n / 1e6).toFixed(1) + 'M'
  if (n >= 1e3) return (n / 1e3).toFixed(1) + 'k'
  return String(n)
}
