// API client. Dev: requests go to /api (Vite proxy -> backend:8000).
// Prod: set VITE_API_URL to the Railway backend URL.
const BASE = import.meta.env.VITE_API_URL || '/api'

async function req(method, path, body) {
  const opts = { method, headers: {} }
  if (body !== undefined) {
    opts.headers['Content-Type'] = 'application/json'
    opts.body = JSON.stringify(body)
  }
  const res = await fetch(`${BASE}${path}`, opts)
  if (!res.ok) {
    let detail
    try { detail = (await res.json()).detail } catch { detail = res.statusText }
    throw new Error(detail || `HTTP ${res.status}`)
  }
  const ct = res.headers.get('content-type') || ''
  return ct.includes('application/json') ? res.json() : res.text()
}

export const api = {
  get: (p) => req('GET', p),
  post: (p, b) => req('POST', p, b),
  patch: (p, b) => req('PATCH', p, b),
  put: (p, b) => req('PUT', p, b),
  del: (p) => req('DELETE', p),

  // multipart upload (CSV import)
  async upload(path, file) {
    const fd = new FormData()
    fd.append('file', file)
    const res = await fetch(`${BASE}${path}`, { method: 'POST', body: fd })
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
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
  if (import.meta.env.VITE_API_URL) {
    return import.meta.env.VITE_API_URL.replace(/^http/, 'ws') + '/ws'
  }
  const proto = location.protocol === 'https:' ? 'wss' : 'ws'
  return `${proto}://${location.host}/ws`
}
