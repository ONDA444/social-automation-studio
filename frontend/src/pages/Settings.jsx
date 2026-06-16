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
    <div className="space-y-6 max-w-3xl fade-in">
      <div>
        <h2 className="heading text-2xl text-gradient font-bold">Configurações</h2>
        <p className="text-text-muted text-sm">Sistema e chaves de API.</p>
      </div>

      <div className="card p-5">
        <div className="flex items-center gap-3 mb-4">
          <h3 className="heading font-semibold">Status dos serviços</h3>
          <div className="flex-1 h-px" style={{ background: 'rgba(255,255,255,0.06)' }} />
        </div>
        {!health ? (
          <div className="space-y-3">
            {[1,2,3].map(i => (
              <div key={i} className="skeleton h-8 rounded-card" />
            ))}
          </div>
        ) : (
          <div className="space-y-1">
            {Object.entries(health.checks).map(([k, v]) => (
              <div key={k} className="flex items-center gap-3 py-2.5 border-b last:border-0" style={{ borderColor: 'rgba(255,255,255,0.05)' }}>
                <span className="w-2.5 h-2.5 rounded-full shrink-0" style={{ background: dot(v.status), boxShadow: v.status === 'green' ? '0 0 6px rgba(0,245,160,0.6)' : 'none' }} />
                <span className="font-medium text-sm capitalize flex-1">{k}</span>
                <span className="text-xs text-text-muted">{v.detail}</span>
              </div>
            ))}
          </div>
        )}
      </div>

      <div className="card p-5">
        <div className="flex items-center gap-3 mb-1">
          <h3 className="heading font-semibold">Chaves de API</h3>
          <div className="flex-1 h-px" style={{ background: 'rgba(255,255,255,0.06)' }} />
        </div>
        <p className="text-[11px] text-text-muted text-right mb-3">🔐 Chaves salvas somente no servidor</p>
        <p className="text-xs text-text-muted mb-4">
          As chaves são lidas do arquivo <code className="font-mono">.env</code> no servidor (nunca expostas ao navegador).
          Edite o <code className="font-mono">.env</code> e reinicie o backend. Sem chaves, o sistema usa fallbacks offline (roteiro template + imagens placeholder).
        </p>
        <div className="space-y-2">
          {KEYS.map((k) => (
            <div key={k.env} className="flex flex-wrap items-center gap-x-3 gap-y-1 text-sm py-2 border-b last:border-0" style={{ borderColor: 'rgba(255,255,255,0.05)' }}>
              <code className="font-mono text-[12px] text-accent w-full sm:w-48 sm:shrink-0 break-all">{k.env}</code>
              <span className="flex-1 min-w-0">{k.label}</span>
              <span className="badge text-[10px]" style={{ background: k.tier === 'grátis' ? 'rgba(0,214,143,.15)' : 'rgba(255,182,39,.15)', color: k.tier === 'grátis' ? 'var(--success)' : 'var(--warning)' }}>{k.tier}</span>
              <a href={k.url} target="_blank" rel="noreferrer" className="text-text-muted hover:text-accent text-xs shrink-0">obter ↗</a>
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}
