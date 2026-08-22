import { useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../api'
import { PageHeader } from '../components/ui.jsx'

// Renderiza **negrito** e quebras de linha vindos das respostas do Copilot.
function RichText({ text }) {
  return text.split('\n').map((line, i) => (
    <span key={i}>
      {i > 0 && <br />}
      {line.split(/(\*\*[^*]+\*\*)/g).map((part, j) =>
        part.startsWith('**') && part.endsWith('**')
          ? <strong key={j}>{part.slice(2, -2)}</strong>
          : part
      )}
    </span>
  ))
}

function Bubble({ role, children, actions }) {
  const nav = useNavigate()
  const isUser = role === 'user'
  return (
    <div className={`flex ${isUser ? 'justify-end' : 'justify-start'} fade-in`}>
      <div
        className="max-w-[85%] sm:max-w-[75%] rounded-card px-4 py-3 text-sm leading-relaxed"
        style={isUser
          ? { background: 'var(--grad-accent)', color: 'var(--text-inverse)', borderBottomRightRadius: 4 }
          : { background: 'var(--bg-surface)', border: '1px solid var(--border)', color: 'var(--text-primary)', borderBottomLeftRadius: 4 }}
      >
        {children}
        {actions?.length > 0 && (
          <div className="flex flex-wrap gap-2 mt-3">
            {actions.map((a, i) => (
              <button key={i} onClick={() => nav(a.to)}
                className="px-3 py-1.5 rounded-pill text-xs font-semibold transition-colors hover:brightness-110"
                style={{ background: 'var(--accent-dim)', border: '1px solid var(--border-glow)', color: 'var(--accent)' }}>
                {a.label} →
              </button>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}

export default function Copilot() {
  const [messages, setMessages] = useState([])
  const [suggestions, setSuggestions] = useState([])
  const [input, setInput] = useState('')
  const [thinking, setThinking] = useState(false)
  const bottomRef = useRef(null)
  const inputRef = useRef(null)

  useEffect(() => {
    api.get('/copilot/suggestions')
      .then((d) => setSuggestions(d.suggestions || []))
      .catch(() => {})
    setMessages([{
      role: 'assistant',
      text: 'Olá! Sou o Copilot do Social Studio. Respondo perguntas sobre o sistema **somente com dados reais** — fila, aprovações, erros, desempenho e saúde. O que você quer saber?',
    }])
  }, [])

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' })
  }, [messages, thinking])

  async function send(question) {
    const q = (question ?? input).trim()
    if (q.length < 2 || thinking) return
    setInput('')
    setMessages((m) => [...m, { role: 'user', text: q }])
    setThinking(true)
    try {
      const res = await api.post('/copilot/ask', { question: q })
      setMessages((m) => [...m, { role: 'assistant', text: res.answer, actions: res.actions }])
    } catch (e) {
      setMessages((m) => [...m, { role: 'assistant', text: `Não consegui responder agora: ${e.message}` }])
    } finally {
      setThinking(false)
      inputRef.current?.focus()
    }
  }

  return (
    <div className="flex flex-col h-full max-h-[calc(100vh-6rem)] fade-in">
      <PageHeader title="AI Copilot" sub="pergunte sobre o sistema — respostas baseadas apenas em dados reais" />

      <div className="flex-1 overflow-y-auto mt-4 rounded-card p-4 space-y-3"
        style={{ background: 'var(--bg-elevated)', border: '1px solid var(--border)' }}>
        {messages.map((m, i) => (
          <Bubble key={i} role={m.role} actions={m.actions}>
            <RichText text={m.text} />
          </Bubble>
        ))}
        {thinking && (
          <div className="flex justify-start fade-in">
            <div className="rounded-card px-4 py-3 text-sm"
              style={{ background: 'var(--bg-surface)', border: '1px solid var(--border)', color: 'var(--text-muted)' }}>
              <span className="inline-flex gap-1 items-center">
                consultando dados
                <span className="animate-pulse">…</span>
              </span>
            </div>
          </div>
        )}
        <div ref={bottomRef} />
      </div>

      {suggestions.length > 0 && (
        <div className="flex flex-wrap gap-2 mt-3">
          {suggestions.map((s, i) => (
            <button key={i} onClick={() => send(s)}
              className="px-3 py-1.5 rounded-pill text-xs font-medium transition-colors hover:brightness-125"
              style={{ background: 'var(--bg-surface)', border: '1px solid var(--border)', color: 'var(--text-muted)' }}>
              {s}
            </button>
          ))}
        </div>
      )}

      <form
        className="flex gap-2 mt-3"
        onSubmit={(e) => { e.preventDefault(); send() }}
      >
        <input
          ref={inputRef}
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder="Pergunte algo sobre o sistema…"
          className="input flex-1"
          maxLength={500}
          aria-label="Pergunta para o Copilot"
        />
        <button type="submit" className="btn btn-primary" disabled={thinking || input.trim().length < 2}>
          Enviar
        </button>
      </form>
    </div>
  )
}
