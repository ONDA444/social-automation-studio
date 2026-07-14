import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../api'
import StyleDNACard from '../components/StyleDNACard.jsx'
import { PageHeader } from '../components/ui.jsx'

const FALLBACK_CONTENT_TYPES = [
  { value: 'film_recap_ai_images', label: 'Recap (narrado)' },
  { value: 'sports_highlights', label: 'Esportes (highlights)' },
  { value: 'top_list_ranking', label: 'Top / Ranking' },
  { value: 'explainer_curiosity', label: 'Curiosidade / Explicação' },
]

export default function RemixEngine() {
  const [source, setSource] = useState('')
  const [dna, setDna] = useState(null)
  const [title, setTitle] = useState('')
  const [contentType, setContentType] = useState('film_recap_ai_images')
  const [contentTypes, setContentTypes] = useState(FALLBACK_CONTENT_TYPES)
  const [typeDetected, setTypeDetected] = useState(false)
  const [format, setFormat] = useState('long')
  const [analyzing, setAnalyzing] = useState(false)
  const [creating, setCreating] = useState(false)
  const [accounts, setAccounts] = useState([])
  const [accountId, setAccountId] = useState('')
  const nav = useNavigate()

  useEffect(() => {
    api.get('/accounts').then((d) => setAccounts(d.accounts || [])).catch(() => {})
    api.get('/jobs/content-types')
      .then((d) => { const list = Array.isArray(d) ? d : d?.content_types; if (Array.isArray(list) && list.length) setContentTypes(list) })
      .catch(() => setContentTypes(FALLBACK_CONTENT_TYPES))
  }, [])

  const analyze = async () => {
    if (!source.trim()) return
    setAnalyzing(true); setDna(null); setTypeDetected(false)
    try {
      const d = await api.post('/remix/analyze', { source })
      const styleDna = d.style_dna
      setDna(styleDna)
      if (styleDna?.suggested_theme) setTitle(styleDna.suggested_theme)  // auto-detected topic
      if (styleDna?.suggested_format) setFormat(styleDna.suggested_format)  // 9:16/1:1 -> short
      const detected = styleDna?.content_type || styleDna?.template_recommendation  // auto-detected type
      if (detected && contentTypes.some((c) => c.value === detected)) { setContentType(detected); setTypeDetected(true) }
    }
    catch (e) { alert('Falha ao analisar: ' + e.message) } finally { setAnalyzing(false) }
  }
  const create = async () => {
    if (!title.trim()) return alert('Informe o tema do novo vídeo')
    setCreating(true)
    try {
      const account_id = accountId ? Number(accountId) : null
      const acc = accounts.find((a) => a.id === account_id)
      const target_platforms = acc && acc.platform ? [acc.platform] : []
      await api.post('/remix/create', { title, content_type: contentType, format, style_dna: dna, source, account_id, target_platforms })
      nav('/queue')
    } catch (e) { alert(e.message) } finally { setCreating(false) }
  }

  return (
    <div className="space-y-6 max-w-4xl fade-in">
      <PageHeader title="Remix Engine" sub="Analise um vídeo de referência para extrair o StyleDNA (apenas o estilo).">
        <span className="badge text-[10px]" style={{ background: 'var(--accent-dim)', color: 'var(--accent)', border: '1px solid var(--border)' }}>100% original</span>
      </PageHeader>

      <div className="card p-4 flex items-start gap-3" style={{ borderColor: 'rgba(0,245,160,0.2)', background: 'rgba(0,245,160,0.04)' }}>
        <span className="text-lg shrink-0 mt-0.5">🛡️</span>
        <p className="text-xs text-text-muted leading-relaxed">Nenhum frame, áudio ou clipe da referência entra no vídeo final. O StyleDNA captura apenas ritmo, paleta e estrutura narrativa — o conteúdo é criado do zero.</p>
      </div>

      <div className="card p-5">
        <div className="flex items-center gap-2 mb-3">
          <h3 className="heading font-semibold text-sm">Referência de estilo</h3>
          <span className="text-[10px] text-text-muted">YouTube • TikTok • Instagram • arquivo local</span>
        </div>
        <div className="flex gap-2">
          <input className="input" value={source} onChange={(e) => setSource(e.target.value)} placeholder="https://..." />
          <button className="btn-primary whitespace-nowrap" onClick={analyze} disabled={analyzing}>{analyzing ? 'Analisando…' : 'Analisar'}</button>
        </div>
      </div>

      {dna && (
        <div className="grid md:grid-cols-2 gap-5">
          <StyleDNACard dna={dna} />
          <div className="card p-5 space-y-3 h-fit">
            <h3 className="heading font-semibold">Criar vídeo no mesmo estilo</h3>
            <div>
              <label className="text-xs text-text-muted">Tema do novo vídeo</label>
              <input className="input mt-1" value={title} onChange={(e) => setTitle(e.target.value)} placeholder="Ex: A história do navio fantasma" />
              {dna?.suggested_theme && <p className="text-[11px] text-accent mt-1">✨ Tema detectado automaticamente do vídeo — edite se quiser</p>}
            </div>
            <div>
              <label className="text-xs text-text-muted">Tipo de conteúdo</label>
              <select className="input mt-1" value={contentType} onChange={(e) => { setContentType(e.target.value); setTypeDetected(false) }}>
                {contentTypes.map((c) => <option key={c.value} value={c.value}>{c.label}</option>)}
              </select>
              {typeDetected
                ? <p className="text-[11px] text-accent mt-1">✨ Tipo detectado automaticamente do vídeo — edite se quiser</p>
                : <p className="text-[11px] text-text-muted mt-1">O vídeo será SOBRE este tema/tipo. O estilo da referência (cor, ritmo, música) é aplicado por cima.</p>}
            </div>
            <div>
              <label className="text-xs text-text-muted">Formato</label>
              <div className="flex gap-2 mt-1">
                {[
                  { v: 'long', label: '🖥️ Longo', hint: '16:9' },
                  { v: 'short', label: '📱 Shorts', hint: '9:16' },
                ].map((f) => (
                  <button key={f.v} type="button" onClick={() => setFormat(f.v)}
                    className="flex-1 rounded-card border p-2.5 text-left text-xs transition-all duration-200"
                    style={format === f.v
                      ? { borderColor: 'var(--accent)', background: 'rgba(124,106,255,0.12)', color: 'var(--text-primary)' }
                      : { borderColor: 'rgba(255,255,255,0.08)', background: 'rgba(20,20,42,0.4)', color: 'var(--text-muted)' }}>
                    <div className="font-semibold">{f.label}</div>
                    <div className="text-[10px] opacity-70">{f.hint}</div>
                  </button>
                ))}
              </div>
            </div>
            <div>
              <label className="text-xs text-text-muted">Conta de destino — opcional</label>
              <select className="input mt-1" value={accountId} onChange={(e) => setAccountId(e.target.value)}>
                <option value="">— nenhuma —</option>
                {accounts.map((a) => <option key={a.id} value={a.id}>{a.display_name} ({a.platform})</option>)}
              </select>
            </div>
            <button className="btn-primary w-full" onClick={create} disabled={creating}>{creating ? '⏳ Criando…' : '🚀 Gerar vídeo remixado'}</button>
            <p className="text-[11px] text-text-muted">Será criado um job <code>from_remix</code> com este StyleDNA.</p>
          </div>
        </div>
      )}
    </div>
  )
}
