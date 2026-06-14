import { useEffect, useState } from 'react'
import { api } from '../api'
import CalendarView from '../components/CalendarView.jsx'
import { fmtDate } from '../lib'

export default function Schedule() {
  const [events, setEvents] = useState([])
  const [accounts, setAccounts] = useState([])
  const [sel, setSel] = useState('')
  const [cfg, setCfg] = useState({ mode: 'fixed', videos_per_day: 1, post_times: ['19:00'], timezone: 'America/Sao_Paulo', auto_shorts: true, shorts_formats: [1, 2, 4] })
  const [slots, setSlots] = useState([])

  useEffect(() => {
    api.get('/schedule/calendar').then((d) => setEvents(d.events)).catch(() => {})
    api.get('/accounts').then((d) => { setAccounts(d.accounts); if (d.accounts[0]) setSel(String(d.accounts[0].id)) }).catch(() => {})
  }, [])

  useEffect(() => {
    if (!sel) return
    api.get(`/schedule/config/${sel}`).then((c) => setCfg((p) => ({ ...p, ...c, post_times: c.post_times?.length ? c.post_times : p.post_times }))).catch(() => {})
    api.get(`/schedule/${sel}/slots?count=6`).then((d) => setSlots(d.slots)).catch(() => {})
  }, [sel])

  const saveCfg = async () => {
    await api.put(`/schedule/config/${sel}`, cfg)
    const d = await api.get(`/schedule/${sel}/slots?count=6&mode=${cfg.mode}`); setSlots(d.slots)
    alert('Configuração salva')
  }

  return (
    <div className="space-y-6">
      <h2 className="heading text-2xl font-semibold">Agenda</h2>
      <div className="grid lg:grid-cols-3 gap-6">
        <div className="lg:col-span-2"><CalendarView events={events} /></div>
        <div className="card p-5 space-y-3 h-fit">
          <h3 className="heading font-semibold">Configuração de slots</h3>
          <select className="input" value={sel} onChange={(e) => setSel(e.target.value)}>
            <option value="">Selecione a conta</option>
            {accounts.map((a) => <option key={a.id} value={a.id}>{a.display_name} ({a.platform})</option>)}
          </select>
          {sel && (
            <>
              <div>
                <label className="text-xs text-text-muted">Modo</label>
                <select className="input mt-1" value={cfg.mode} onChange={(e) => setCfg({ ...cfg, mode: e.target.value })}>
                  <option value="fixed">Fixo (horários definidos)</option>
                  <option value="smart">Inteligente (melhores horários)</option>
                  <option value="trending_aware">Trending (publicar logo)</option>
                </select>
              </div>
              <div><label className="text-xs text-text-muted">Vídeos por dia</label><input type="number" min="1" max="6" className="input mt-1" value={cfg.videos_per_day} onChange={(e) => setCfg({ ...cfg, videos_per_day: Number(e.target.value) })} /></div>
              <div><label className="text-xs text-text-muted">Horários (vírgula)</label><input className="input mt-1" value={(cfg.post_times || []).join(', ')} onChange={(e) => setCfg({ ...cfg, post_times: e.target.value.split(',').map((s) => s.trim()) })} placeholder="19:00, 21:00" /></div>
              <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={cfg.auto_shorts} onChange={(e) => setCfg({ ...cfg, auto_shorts: e.target.checked })} /> Gerar Shorts automaticamente</label>
              <button className="btn-primary w-full" onClick={saveCfg}>Salvar</button>

              <div className="pt-2 border-t border-border">
                <p className="text-xs text-text-muted mb-1">Próximos horários sugeridos:</p>
                <ul className="text-sm space-y-1">{slots.map((s, i) => <li key={i} className="font-mono text-[12px]">{fmtDate(s)}</li>)}</ul>
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  )
}
