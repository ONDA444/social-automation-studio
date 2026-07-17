import { useEffect, useState } from 'react'
import { api } from '../api'
import { PageHeader, SectionCard } from '../components/ui.jsx'

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

function Toggle({ on, onChange }) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={on}
      onClick={() => onChange(!on)}
      className="relative shrink-0 rounded-full transition-colors"
      style={{
        width: 42, height: 24,
        background: on ? 'var(--success)' : 'rgba(255,255,255,0.14)',
      }}
    >
      <span
        className="absolute top-0.5 rounded-full bg-white transition-all"
        style={{ width: 20, height: 20, left: on ? 20 : 2 }}
      />
    </button>
  )
}

function MonetizationCard() {
  const [cfg, setCfg] = useState(null)
  const [saving, setSaving] = useState(false)
  const [msg, setMsg] = useState(null)

  useEffect(() => {
    api.get('/settings/monetization').then(setCfg).catch((e) => setMsg({ ok: false, text: e.message }))
  }, [])

  const save = async () => {
    if (!cfg) return
    setSaving(true); setMsg(null)
    try {
      const next = await api.put('/settings/monetization', cfg)
      setCfg(next)
      setMsg({ ok: true, text: 'Salvo. Vale para os próximos vídeos gerados.' })
    } catch (e) {
      setMsg({ ok: false, text: e.message })
    } finally {
      setSaving(false)
    }
  }

  const field = (k, v) => setCfg((c) => ({ ...c, [k]: v }))

  if (!cfg) {
    return (
      <SectionCard title="Monetização">
        <div className="skeleton h-24 rounded-card" />
      </SectionCard>
    )
  }

  const inputStyle = {
    background: 'var(--bg-base, rgba(0,0,0,0.25))',
    border: '1px solid var(--border)',
    color: 'var(--text-primary)',
  }

  return (
    <SectionCard title="Monetização" sub="Receita que não depende do YPP">
      {/* CTA de afiliado / produto */}
      <div className="flex items-start gap-3 mb-2">
        <Toggle on={cfg.monetization_enabled} onChange={(v) => field('monetization_enabled', v)} />
        <div className="flex-1">
          <p className="font-medium text-sm">CTA de afiliado / produto</p>
          <p className="text-xs text-text-muted">
            Vai no <strong>topo</strong> da descrição de cada vídeo do YouTube — onde mais converte.
            Cole seus links de afiliado, produto ou newsletter. Inclua a frase de divulgação (ex.: “Links de afiliado”).
          </p>
        </div>
      </div>
      <textarea
        value={cfg.monetization_cta || ''}
        onChange={(e) => field('monetization_cta', e.target.value)}
        disabled={!cfg.monetization_enabled}
        rows={4}
        placeholder={'🔗 Ferramentas que eu uso: https://...\n📩 Newsletter grátis: https://...\n(Links de afiliado)'}
        className="w-full rounded-card px-3 py-2 text-sm font-mono resize-y disabled:opacity-40 mb-6"
        style={inputStyle}
      />

      {/* Idiomas extras */}
      <div className="flex items-start gap-3 mb-2">
        <Toggle on={cfg.localize_enabled} onChange={(v) => field('localize_enabled', v)} />
        <div className="flex-1">
          <p className="font-medium text-sm">Títulos/descrições em outros idiomas</p>
          <p className="text-xs text-text-muted">
            Alcance internacional grátis: o vídeo ganha título e descrição traduzidos.
            Códigos ISO separados por vírgula. Ex.: <code className="font-mono">en,es,hi</code>.
            Recomendo testar em 1 vídeo antes de ligar para todos.
          </p>
        </div>
      </div>
      <input
        type="text"
        value={cfg.localize_languages || ''}
        onChange={(e) => field('localize_languages', e.target.value)}
        disabled={!cfg.localize_enabled}
        placeholder="en,es,hi"
        className="w-full rounded-card px-3 py-2 text-sm font-mono disabled:opacity-40"
        style={inputStyle}
      />
      {cfg.localize_enabled && (() => {
        const raw = cfg.localize_languages || '';
        const codes = raw
          .split(/[,;]/)
          .map((c) => c.trim())
          .filter(Boolean);
        if (codes.length === 0) return null;
        const invalid = codes.filter((c) => !/^[a-zA-Z]{2,3}$/.test(c));
        if (invalid.length > 0) {
          return (
            <p className="text-xs mt-1" style={{ color: '#e0a30f' }}>
              Código{invalid.length > 1 ? 's' : ''} suspeito{invalid.length > 1 ? 's' : ''}: {invalid.join(', ')}.
              Use códigos ISO de 2-3 letras (ex.: en, es, hi), não o nome do idioma.
            </p>
          );
        }
        return (
          <p className="text-xs text-text-muted mt-1">
            Será salvo como: <code className="font-mono">{codes.map((c) => c.toLowerCase()).join(',')}</code>
          </p>
        );
      })()}

      <div className="flex items-center gap-3 mt-5">
        <button
          type="button"
          onClick={save}
          disabled={saving}
          className="rounded-card px-4 py-2 text-sm font-semibold disabled:opacity-50"
          style={{ background: 'var(--accent)', color: '#04110b' }}
        >
          {saving ? 'Salvando…' : 'Salvar'}
        </button>
        {msg && (
          <span className="text-xs" style={{ color: msg.ok ? 'var(--success)' : 'var(--error)' }}>
            {msg.text}
          </span>
        )}
      </div>
    </SectionCard>
  )
}

function FixErrorsCard() {
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState(null)

  const run = async () => {
    if (!confirm('Isso vai reenviar automaticamente todos os vídeos com erro (exceto os que arriscam duplicar um envio já feito). Continuar?')) return
    setBusy(true); setResult(null)
    try {
      const r = await api.post('/jobs/fix-errors', {})
      setResult(r)
    } catch (e) {
      setResult({ error: e.message })
    } finally {
      setBusy(false)
    }
  }

  return (
    <SectionCard title="Corrigir erros" sub="Reenvia de uma vez todo vídeo travado com erro na Fila.">
      <p className="text-xs text-text-muted mb-4">
        Ao clicar, o sistema recoloca na fila e tenta publicar de novo, agora, todo vídeo com status
        de erro — token do Google expirado, limite de upload do YouTube, falha de rede no Drive, cota
        de IA esgotada, etc. Vídeos onde reenviar poderia duplicar um envio já feito (ex.: upload
        interrompido no meio) ficam de fora e continuam precisando de conferência manual.
      </p>
      <button
        type="button"
        onClick={run}
        disabled={busy}
        className="rounded-card px-4 py-2 text-sm font-semibold disabled:opacity-50"
        style={{ background: 'var(--accent)', color: '#04110b' }}
      >
        {busy ? 'Corrigindo…' : '🔧 Corrigir erros automaticamente'}
      </button>

      {result && !result.error && (
        <div className="mt-3 text-xs space-y-1">
          <p style={{ color: 'var(--success)' }}>
            {result.fixed_count} vídeo{result.fixed_count === 1 ? '' : 's'} reenviado{result.fixed_count === 1 ? '' : 's'} agora.
          </p>
          {result.skipped_count > 0 && (
            <div style={{ color: 'var(--warning)' }}>
              <p>{result.skipped_count} deixado{result.skipped_count === 1 ? '' : 's'} de fora (precisa conferir manualmente):</p>
              <ul className="list-disc ml-4 mt-1 space-y-0.5">
                {result.skipped.map((s) => (
                  <li key={s.id}><strong>{s.title}</strong> — {s.reason}</li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}
      {result?.error && (
        <p className="mt-3 text-xs" style={{ color: 'var(--error)' }}>{result.error}</p>
      )}
    </SectionCard>
  )
}

export default function Settings() {
  const [health, setHealth] = useState(null)
  useEffect(() => { api.get('/dashboard/health').then(setHealth).catch(() => {}) }, [])

  const dot = (s) => ({ green: 'var(--success)', yellow: 'var(--warning)', red: 'var(--error)' }[s] || 'var(--text-muted)')

  return (
    <div className="space-y-6 max-w-3xl fade-in">
      <PageHeader title="Configurações" sub="Sistema, monetização e chaves de API." />

      <SectionCard title="Status dos serviços">
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
      </SectionCard>

      <FixErrorsCard />

      <MonetizationCard />

      <SectionCard title="Chaves de API">
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
      </SectionCard>
    </div>
  )
}
