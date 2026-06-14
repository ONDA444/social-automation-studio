import { useEffect, useState } from 'react'
import { api } from '../api'

const KEYS = [
  { env: 'GROQ_API_KEY', label: 'Groq (scripts/SEO)', tier: 'grátis', url: 'https://console.groq.com' },
  { env: 'GEMINI_API_KEY', label: 'Google Gemini (fallback)', tier: 'grátis', url: 'https://aistudio.google.com' },
  { env: 'POLLINATIONS_TOKEN', label: 'Pollinations (imagens IA)', tier: 'grátis', url: 'https://enter.pollinations.ai' },
  { env: 'HUGGINGFACE_TOKEN', label: 'HuggingFace FLUX (imagens)', tier: 'grátis', url: 'https://huggingface.co/settings/tokens' },
  { env: 'PEXELS_API_KEY', label: 'Pexels (stock footage)', tier: 'grátis', url: 'https://www.pexels.com/api' },
  { env: 'GOOGLE_CLIENT_ID', label: 'YouTube OAuth', tier: 'grátis', url: 'https://console.cloud.google.com' },
  { env: 'TIKTOK_CLIENT_KEY', label: 'TikTok Content Posting (aprovação 3-5 dias)', tier: 'aprovação', url: 'https://developers.tiktok.com' },
  { env: 'META_APP_ID', label: 'Instagram / Meta Graph', tier: 'grátis', url: 'https://developers.facebook.com' },
]

export default function Settings() {
  const [health, setHealth] = useState(null)
  useEffect(() => { api.get('/dashboard/health').then(setHealth).catch(() => {}) }, [])

  const dot = (s) => ({ green: 'var(--success)', yellow: 'var(--warning)', red: 'var(--error)' }[s] || 'var(--text-muted)')

  return (
    <div className="space-y-6 max-w-3xl">
      <h2 className="heading text-2xl font-semibold">Configurações</h2>

      <div className="card p-5">
        <h3 className="heading font-semibold mb-3">Status dos serviços</h3>
        {!health ? <p className="text-text-muted text-sm">Carregando…</p> : (
          <div className="space-y-2">
            {Object.entries(health.checks).map(([k, v]) => (
              <div key={k} className="flex items-center gap-3 text-sm">
                <span className="w-2.5 h-2.5 rounded-full" style={{ background: dot(v.status) }} />
                <span className="font-medium w-24 capitalize">{k}</span>
                <span className="text-text-muted">{v.detail}</span>
              </div>
            ))}
          </div>
        )}
      </div>

      <div className="card p-5">
        <h3 className="heading font-semibold mb-1">Chaves de API</h3>
        <p className="text-xs text-text-muted mb-4">
          As chaves são lidas do arquivo <code className="font-mono">.env</code> no servidor (nunca expostas ao navegador).
          Edite o <code className="font-mono">.env</code> e reinicie o backend. Sem chaves, o sistema usa fallbacks offline (roteiro template + imagens placeholder).
        </p>
        <div className="space-y-2">
          {KEYS.map((k) => (
            <div key={k.env} className="flex items-center gap-3 text-sm py-1.5 border-b border-border/50 last:border-0">
              <code className="font-mono text-[12px] text-accent w-48 shrink-0">{k.env}</code>
              <span className="flex-1">{k.label}</span>
              <span className="badge text-[10px]" style={{ background: k.tier === 'grátis' ? 'rgba(0,214,143,.15)' : 'rgba(255,182,39,.15)', color: k.tier === 'grátis' ? 'var(--success)' : 'var(--warning)' }}>{k.tier}</span>
              <a href={k.url} target="_blank" rel="noreferrer" className="text-text-muted hover:text-accent text-xs">obter ↗</a>
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}
