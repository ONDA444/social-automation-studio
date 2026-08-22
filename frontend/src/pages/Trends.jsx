import { useEffect, useState } from 'react'
import { api } from '../api'
import { useAccounts } from '../AccountsContext.jsx'
import { PageHeader, SectionCard, EmptyState, ErrorBanner, useToast } from '../components/ui.jsx'

// Página Tendências (§15) — Content Intelligence: busca o que está em alta
// (Google Trends + LLM, via /dashboard/trending) e transforma qualquer
// sugestão num vídeo real na Fila com um clique. Nada é simulado: a lista vem
// do TrendingAgent e o botão cria um job de verdade.

const SOURCE_META = {
  google_trends: { label: 'Google Trends', color: 'var(--accent)', icon: '📈' },
  llm: { label: 'IA', color: 'var(--warning)', icon: '🤖' },
  fallback: { label: 'Padrão', color: 'var(--text-muted)', icon: '•' },
}

function SuggestionCard({ s, onCreate, creating }) {
  const meta = SOURCE_META[s.source] || SOURCE_META.fallback
  return (
    <div className="rounded-card p-4 flex flex-col gap-3 fade-in card-hover"
      style={{ background: 'var(--bg-surface)', border: '1px solid var(--border)' }}>
      <div className="flex items-start justify-between gap-2">
        <p className="text-sm font-bold leading-snug" style={{ color: 'var(--text-primary)' }}>{s.topic}</p>
        <span className="badge text-[9px] shrink-0"
          style={{ background: 'var(--bg-elevated)', color: meta.color, border: '1px solid var(--border)' }}>
          {meta.icon} {meta.label}
        </span>
      </div>
      <div>
        <div className="flex items-center justify-between text-[10px] mb-1" style={{ color: 'var(--text-dim)' }}>
          <span>{s.reason}</span>
          <span className="font-mono">{s.score}</span>
        </div>
        <div className="h-1.5 rounded-full overflow-hidden" style={{ background: 'var(--bg-elevated)' }}>
          <div className="h-full rounded-full" style={{ width: `${s.score}%`, background: 'var(--grad-accent)' }} />
        </div>
      </div>
      <button
        className="btn-primary btn-sm self-start"
        disabled={creating}
        onClick={() => onCreate(s)}
      >
        {creating ? 'Criando…' : '+ Criar vídeo'}
      </button>
    </div>
  )
}

export default function Trends() {
  const { accounts } = useAccounts()
  const [niche, setNiche] = useState('entretenimento')
  const [accountId, setAccountId] = useState('')
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(false)
  const [loadError, setLoadError] = useState(false)
  const [creatingTopic, setCreatingTopic] = useState(null)
  const toast = useToast()

  // Nichos dos canais reais como sugestões rápidas de busca.
  const niches = [...new Set((accounts || []).map((a) => a.niche).filter(Boolean))]

  const search = (n = niche) => {
    if (!n.trim()) return
    setLoading(true)
    setLoadError(false)
    api.get(`/dashboard/trending?niche=${encodeURIComponent(n.trim())}`)
      .then((d) => setData(d))
      .catch(() => setLoadError(true))
      .finally(() => setLoading(false))
  }

  useEffect(() => { search() }, []) // primeira busca automática com o nicho padrão

  const createVideo = async (s) => {
    setCreatingTopic(s.topic)
    try {
      await api.post('/jobs', {
        title: s.topic,
        topic: data?.niche || niche,
        content_type: 'auto',
        account_id: accountId ? Number(accountId) : null,
      })
      toast.success(`Vídeo criado na Fila: "${s.topic}"`)
    } catch (e) {
      toast.error(e.message)
    } finally {
      setCreatingTopic(null)
    }
  }

  return (
    <div className="space-y-4">
      <PageHeader
        title="Tendências"
        sub="O que está em alta agora — e vira vídeo com um clique."
      />

      <div className="card p-3 flex items-center gap-3 flex-wrap">
        <input
          className="input text-sm sm:max-w-[280px]"
          value={niche}
          onChange={(e) => setNiche(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && search()}
          placeholder="Nicho (ex.: futebol, filmes, tecnologia)"
          list="trend-niches"
        />
        <datalist id="trend-niches">
          {niches.map((n) => <option key={n} value={n} />)}
        </datalist>
        <select
          className="input text-sm sm:w-52"
          value={accountId}
          onChange={(e) => setAccountId(e.target.value)}
          title="Canal de destino dos vídeos criados a partir das sugestões"
        >
          <option value="">— sem canal —</option>
          {(accounts || []).map((a) => (
            <option key={a.id} value={a.id}>{a.display_name}{a.platform ? ` (${a.platform})` : ''}</option>
          ))}
        </select>
        <button className="btn btn-primary text-sm" onClick={() => search()} disabled={loading}>
          {loading ? 'Buscando…' : '🔍 Buscar tendências'}
        </button>
      </div>

      {loadError && (
        <ErrorBanner message="Não foi possível buscar tendências agora." onRetry={() => search()} />
      )}

      <SectionCard>
        {loading && !data ? (
          <div className="grid sm:grid-cols-2 lg:grid-cols-3 gap-3">
            {Array.from({ length: 6 }).map((_, i) => <div key={i} className="skeleton h-32 rounded-card" />)}
          </div>
        ) : !data || data.suggestions?.length === 0 ? (
          <EmptyState
            icon="📈"
            title="Nenhuma tendência encontrada"
            hint="Tente outro nicho ou busque novamente em alguns minutos."
          />
        ) : (
          <>
            <p className="text-[11px] mb-3" style={{ color: 'var(--text-dim)' }}>
              {data.suggestions.length} sugestões para "{data.niche}" — dados do Google Trends e da IA.
              O vídeo criado entra na Fila e segue o pipeline normal (aprovação inclusa).
            </p>
            <div className="grid sm:grid-cols-2 lg:grid-cols-3 gap-3 stagger">
              {data.suggestions.map((s) => (
                <SuggestionCard key={s.topic} s={s} onCreate={createVideo}
                  creating={creatingTopic === s.topic} />
              ))}
            </div>
          </>
        )}
      </SectionCard>
    </div>
  )
}
