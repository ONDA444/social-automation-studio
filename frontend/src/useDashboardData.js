import { useCallback, useEffect, useState } from 'react'
import { api } from './api'

// Shared /dashboard + /dashboard/health poll. Both the TopBar (active-jobs
// badge, API health chip) and the Dashboard page (full payload) used to run
// their own independent 8s pollers against the same endpoints — this hook is
// mounted once in AppLayout and both consume the result instead.
//
// On failure, keep any previously-loaded data (don't overwrite good data with
// an error) but flag it as stale so the UI can warn the user it's not live.
export function useDashboardData(count) {
  const [data, setData] = useState(null)
  const [health, setHealth] = useState('green')
  const [healthDetail, setHealthDetail] = useState(null)
  const [stale, setStale] = useState(false)

  const load = useCallback(() => Promise.all([
    api.get('/dashboard'),
    api.get('/dashboard/health'),
  ]).then(([d, h]) => {
    setData(d)
    setHealth(h.status)
    setHealthDetail(h)
    setStale(false)
  }).catch(() => {
    setStale(true)
    setHealth('red')
    setData((prev) => prev || { error: true })
  }), [])

  useEffect(() => {
    let t
    const poll = () => { load().finally(() => { t = setTimeout(poll, 8000) }) }
    poll()
    return () => clearTimeout(t)
  }, [load])

  // Refresh sooner when pipeline events arrive instead of waiting for the next tick.
  useEffect(() => { const t = setTimeout(load, 800); return () => clearTimeout(t) }, [count, load])

  return { data, health, healthDetail, stale, refresh: load }
}
