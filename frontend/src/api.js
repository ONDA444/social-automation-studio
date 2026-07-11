// API client. Dev: requests go to /api (Vite proxy -> backend:8000).
// Prod on Railway (same origin): the backend serves the bundled SPA, so the
// relative '/api' default works. Prod on Vercel (different origin): a relative
// '/api' would hit Vercel's static host — the SPA catch-all returns index.html
// (HTML, not JSON), so every page stalls on its skeleton loader. Fall back to
// the Railway backend by absolute URL there (CORS already allows *.vercel.app).
// Override either case with VITE_API_URL.
const RAILWAY_BACKEND = 'https://backend-production-d314e.up.railway.app'
const _envBase = (import.meta.env.VITE_API_URL || '').trim()
const _onVercel = typeof location !== 'undefined' && location.hostname.endsWith('.vercel.app')
export const API_BASE = _envBase || (_onVercel ? RAILWAY_BACKEND : '/api')
const BASE = API_BASE

async function req(method, path, body, { timeoutMs } = {}) {
  const opts = { method, headers: {} }
  if (body !== undefined) {
    opts.headers['Content-Type'] = 'application/json'
    opts.body = JSON.stringify(body)
  }
  // Optional timeout so a stalled request can never hang the UI forever.
  let timer
  if (timeoutMs) {
    const ctrl = new AbortController()
    opts.signal = ctrl.signal
    timer = setTimeout(() => ctrl.abort(), timeoutMs)
  }
  let res
  try {
    res = await fetch(`${BASE}${path}`, opts)
  } catch (e) {
    if (e?.name === 'AbortError') {
      throw new Error('A operação demorou demais e foi cancelada. Tente de novo.')
    }
    throw e
  } finally {
    if (timer) clearTimeout(timer)
  }
  if (!res.ok) {
    let detail
    try { detail = (await res.json()).detail } catch { detail = undefined }
    let message
    if (typeof detail === 'string') {
      message = detail
    } else if (Array.isArray(detail)) {
      message = detail.map((item) => (item && item.msg) ? item.msg : JSON.stringify(item)).join('; ')
    } else if (detail && typeof detail === 'object') {
      message = JSON.stringify(detail)
    } else {
      message = res.statusText || `HTTP ${res.status}`
    }
    throw new Error(message || res.statusText || `HTTP ${res.status}`)
  }
  const ct = res.headers.get('content-type') || ''
  return ct.includes('application/json') ? res.json() : res.text()
}

export const api = {
  get: (p, opts) => req('GET', p, undefined, opts),
  post: (p, b, opts) => req('POST', p, b, opts),
  patch: (p, b, opts) => req('PATCH', p, b, opts),
  put: (p, b, opts) => req('PUT', p, b, opts),
  del: (p, opts) => req('DELETE', p, undefined, opts),

  // multipart upload (CSV import, voice recording, manual video upload)
  async upload(path, file, fields) {
    const fd = new FormData()
    fd.append('file', file)
    for (const [k, v] of Object.entries(fields || {})) {
      if (v !== undefined && v !== null && v !== '') fd.append(k, v)
    }
    const res = await fetch(`${BASE}${path}`, { method: 'POST', body: fd })
    if (!res.ok) {
      let msg
      try { const d = (await res.json()).detail; msg = typeof d === 'string' ? d : JSON.stringify(d) } catch { msg = `HTTP ${res.status}` }
      throw new Error(msg || `HTTP ${res.status}`)
    }
    return res.json()
  },
}

// Build a preview URL for a generated artifact (absolute server path).
export function mediaUrl(absPath) {
  if (!absPath) return null
  return `${BASE}/media?path=${encodeURIComponent(absPath)}`
}

// WebSocket URL for the live AgentLog.
export function wsUrl() {
  if (API_BASE.startsWith('http')) {
    return API_BASE.replace(/^http/, 'ws') + '/ws'
  }
  const proto = location.protocol === 'https:' ? 'wss' : 'ws'
  return `${proto}://${location.host}/ws`
}
