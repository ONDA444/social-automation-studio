import { useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../api'
import ChannelCard from '../components/ChannelCard.jsx'
import { PageHeader, SectionCard, EmptyState } from '../components/ui.jsx'
import { INTRO_MODE_OPTIONS, PLATFORM_META } from '../lib'

const EMPTY_FORM = {
  account_id: '', name: '', intro_mode: 'mixed',
  daily_limit_long: 1, daily_limit_short: 3,
  posting_window_start: '08:00', posting_window_end: '23:00',
}

export default function Channels() {
  const [channels, setChannels] = useState([])
  const [accounts, setAccounts] = useState([])
  const [loaded, setLoaded] = useState(false)
  const [adding, setAdding] = useState(false)
  const [creating, setCreating] = useState(false)
  const [form, setForm] = useState(EMPTY_FORM)

  const load = () => Promise.all([
    api.get('/channels').then((d) => setChannels(d.channels || [])).catch(() => {}),
    api.get('/accounts').then((d) => setAccounts(d.accounts || [])).catch(() => {}),
  ]).then(() => setLoaded(true))

  useEffect(() => { load() }, [])

  const linkedAccountIds = useMemo(() => new Set(channels.map((c) => c.account_id)), [channels])
  const availableAccounts = useMemo(() => accounts.filter((a) => !linkedAccountIds.has(a.id)), [accounts, linkedAccountIds])
  const accountById = useMemo(() => Object.fromEntries(accounts.map((a) => [a.id, a])), [accounts])

  const totals = useMemo(() => ({
    all: channels.length,
    active: channels.filter((c) => c.active).length,
    unlinked: availableAccounts.length,
  }), [channels, availableAccounts])

  const openAdding = () => {
    setForm({ ...EMPTY_FORM, account_id: availableAccounts[0] ? String(availableAccounts[0].id) : '' })
    setAdding((v) => !v)
  }

  const create = async () => {
    if (!form.account_id) return alert('Selecione a conta que este canal vai gerenciar')
    if (creating) return
    const account = accountById[Number(form.account_id)]
    setCreating(true)
    try {
      await api.post('/channels', {
        account_id: Number(form.account_id),
        name: form.name.trim() || account?.display_name || 'Canal',
        intro_mode: form.intro_mode,
        daily_limit_long: Number(form.daily_limit_long),
        daily_limit_short: Number(form.daily_limit_short),
        posting_window_start: form.posting_window_start,
        posting_window_end: form.posting_window_end,
      })
      setForm(EMPTY_FORM)
      setAdding(false)
      load()
    } catch (err) {
      alert(err?.message || 'Falha ao criar canal')
    } finally {
      setCreating(false)
    }
  }

  return (
    <div className="space-y-4 sm:space-y-5 fade-in">
      <PageHeader title="Canais" sub="Identidade visual, limites diarios, janela de publicacao e agenda de cada canal.">
        {accounts.length > 0 && (
          <button className="btn btn-primary w-full sm:w-auto" onClick={openAdding}>
            {adding ? 'Fechar' : 'Novo canal'}
          </button>
        )}
      </PageHeader>

      <div className="grid grid-cols-3 gap-2 sm:gap-3">
        <div className="card p-3 sm:p-4">
          <p className="text-[10px] sm:text-xs font-semibold uppercase" style={{ color: 'var(--text-muted)', letterSpacing: '.06em' }}>Canais</p>
          <p className="data text-xl sm:text-2xl font-black mt-1">{totals.all}</p>
        </div>
        <div className="card p-3 sm:p-4">
          <p className="text-[10px] sm:text-xs font-semibold uppercase" style={{ color: 'var(--text-muted)', letterSpacing: '.06em' }}>Ativos</p>
          <p className="data text-xl sm:text-2xl font-black mt-1" style={{ color: 'var(--success)' }}>{totals.active}</p>
        </div>
        <div className="card p-3 sm:p-4">
          <p className="text-[10px] sm:text-xs font-semibold uppercase truncate" style={{ color: 'var(--text-muted)', letterSpacing: '.06em' }}>Sem canal</p>
          <p className="data text-xl sm:text-2xl font-black mt-1" style={{ color: totals.unlinked ? 'var(--warning)' : 'var(--text-primary)' }}>{totals.unlinked}</p>
        </div>
      </div>

      {adding && (
        <SectionCard className="grid sm:grid-cols-2 gap-3 slide-down">
          <label className="space-y-1">
            <span className="text-sm font-semibold" style={{ color: 'var(--text-muted)' }}>Conta</span>
            <select className="input" value={form.account_id} onChange={(e) => setForm({ ...form, account_id: e.target.value })}>
              {availableAccounts.length === 0 && <option value="">Nenhuma conta disponivel</option>}
              {availableAccounts.map((a) => {
                const meta = PLATFORM_META[a.platform] || { label: a.platform }
                return <option key={a.id} value={a.id}>{meta.label} - {a.display_name}</option>
              })}
            </select>
          </label>
          <label className="space-y-1">
            <span className="text-sm font-semibold" style={{ color: 'var(--text-muted)' }}>Nome do canal</span>
            <input className="input" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })}
              placeholder={accountById[Number(form.account_id)]?.display_name || 'Nome'} />
          </label>
          <label className="space-y-1">
            <span className="text-sm font-semibold" style={{ color: 'var(--text-muted)' }}>Nicho (da conta)</span>
            <input className="input" disabled
              value={accountById[Number(form.account_id)]?.niche || 'Nao definido'}
              title="O nicho pertence a conta -- edite em Plataformas." />
          </label>
          <label className="space-y-1">
            <span className="text-sm font-semibold" style={{ color: 'var(--text-muted)' }}>Modo de intro</span>
            <select className="input" value={form.intro_mode} onChange={(e) => setForm({ ...form, intro_mode: e.target.value })}>
              {INTRO_MODE_OPTIONS.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
            </select>
          </label>
          <label className="space-y-1">
            <span className="text-sm font-semibold" style={{ color: 'var(--text-muted)' }}>Longos por dia</span>
            <input type="number" min="0" max="20" className="input" value={form.daily_limit_long}
              onChange={(e) => setForm({ ...form, daily_limit_long: e.target.value })} />
          </label>
          <label className="space-y-1">
            <span className="text-sm font-semibold" style={{ color: 'var(--text-muted)' }}>Shorts por dia</span>
            <input type="number" min="0" max="50" className="input" value={form.daily_limit_short}
              onChange={(e) => setForm({ ...form, daily_limit_short: e.target.value })} />
          </label>
          <label className="space-y-1">
            <span className="text-sm font-semibold" style={{ color: 'var(--text-muted)' }}>Janela - inicio</span>
            <input type="time" className="input" value={form.posting_window_start}
              onChange={(e) => setForm({ ...form, posting_window_start: e.target.value })} />
          </label>
          <label className="space-y-1">
            <span className="text-sm font-semibold" style={{ color: 'var(--text-muted)' }}>Janela - fim</span>
            <input type="time" className="input" value={form.posting_window_end}
              onChange={(e) => setForm({ ...form, posting_window_end: e.target.value })} />
          </label>
          <button className="btn btn-primary w-full sm:col-span-2" onClick={create} disabled={creating || availableAccounts.length === 0}>
            {creating ? 'Criando...' : 'Criar canal'}
          </button>
        </SectionCard>
      )}

      {loaded && accounts.length === 0 && (
        <div className="rounded-card" style={{ background: 'var(--bg-surface)', border: '1px solid var(--border)' }}>
          <EmptyState
            icon="CH"
            title="Nenhuma conta conectada"
            hint="Um canal gerencia identidade visual, limites e agenda em cima de uma conta ja conectada. Conecte uma conta primeiro."
            action={<Link className="btn btn-primary" to="/platforms">Ir para Plataformas</Link>}
          />
        </div>
      )}

      {loaded && accounts.length > 0 && channels.length === 0 && !adding && (
        <div className="rounded-card" style={{ background: 'var(--bg-surface)', border: '1px solid var(--border)' }}>
          <EmptyState
            icon="CH"
            title="Nenhum canal configurado"
            hint="Crie um canal para vincular uma identidade visual, limites diarios e agenda a uma conta conectada."
            action={<button className="btn btn-primary" onClick={openAdding}>Novo canal</button>}
          />
        </div>
      )}

      {channels.length > 0 && (
        <div className="grid lg:grid-cols-2 gap-3">
          {channels.map((c) => (
            <ChannelCard key={c.id} channel={c} account={accountById[c.account_id]} onChange={load} />
          ))}
        </div>
      )}
    </div>
  )
}
