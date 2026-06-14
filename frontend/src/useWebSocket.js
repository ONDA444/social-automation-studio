import { useEffect, useRef, useState } from 'react'
import { wsUrl } from './api'

// Subscribes to the backend AgentLog stream. Auto-reconnects.
// Returns { events, connected }. `events` keeps the last `max` messages.
export function useWebSocket(max = 200) {
  const [events, setEvents] = useState([])
  const [connected, setConnected] = useState(false)
  const wsRef = useRef(null)

  useEffect(() => {
    let alive = true
    let retry

    function connect() {
      if (!alive) return
      let ws
      try {
        ws = new WebSocket(wsUrl())
      } catch {
        retry = setTimeout(connect, 3000)
        return
      }
      wsRef.current = ws
      ws.onopen = () => setConnected(true)
      ws.onclose = () => {
        setConnected(false)
        if (alive) retry = setTimeout(connect, 3000)
      }
      ws.onerror = () => ws.close()
      ws.onmessage = (e) => {
        try {
          const msg = JSON.parse(e.data)
          msg._t = Date.now()
          setEvents((prev) => [...prev.slice(-(max - 1)), msg])
        } catch { /* ignore */ }
      }
    }
    connect()
    return () => {
      alive = false
      clearTimeout(retry)
      wsRef.current?.close()
    }
  }, [max])

  return { events, connected }
}
