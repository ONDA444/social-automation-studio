import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../api'
import StyleDNACard from '../components/StyleDNACard.jsx'

export default function RemixEngine() {
  const [source, setSource] = useState('')
  const [dna, setDna] = useState(null)
  const [title, setTitle] = useState('')
  const [analyzing, setAnalyzing] = useState(false)
  const [creating, setCreating] = useState(false)
  const nav = useNavigate()

  const analyze = async () => {
    if (!source.trim()) return
    setAnalyzing(true); setDna(null)
    try { const d = await api.post('/remix/analyze', { source }); setDna(d.style_dna) }
    catch (e) { alert('Falha ao analisar: ' + e.message) } finally { setAnalyzing(false) }
  }
  const create = async () => {
    if (!title.trim()) return alert('Informe o tema do novo vídeo')
    setCreating(true)
    try { await api.post('/remix/create', { title, style_dna: dna, source }); nav('/queue') }
    catch (e) { alert(e.message) } finally { setCreating(false) }
  }

  return (
    <div className="space-y-6 max-w-4xl">
      <h2 className="heading text-2xl font-semibold">Remix Engine</h2>
      <p className="text-sm text-text-muted">
        Analise um vídeo de referência para extrair o <b>StyleDNA</b> (apenas o estilo).
        Nenhum frame, áudio ou clipe da referência entra no vídeo final — o conteúdo é 100% original.
      </p>

      <div className="card p-5">
        <label className="text-xs text-text-muted">URL de referência (YouTube, TikTok, Instagram) ou caminho local</label>
        <div className="flex gap-2 mt-1">
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
            </div>
            <button className="btn-primary w-full" onClick={create} disabled={creating}>{creating ? 'Criando…' : 'Gerar vídeo remixado'}</button>
            <p className="text-[11px] text-text-muted">Será criado um job <code>from_remix</code> com este StyleDNA.</p>
          </div>
        </div>
      )}
    </div>
  )
}
