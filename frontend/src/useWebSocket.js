import { useEffect, useRef, useState } from 'react'
import { wsUrl } from './api'

// Subscribes to the backend AgentLog stream. Auto-reconnects.
// Returns { events, connected, count }. `events` keeps the last `max` messages.
//
// Incoming messages are BUFFERED and flushed on a fixed cadence (flushMs) rather
// than written to state one-by-one. During heavy generation the pipeline emits
// dozens of events per second; writing each one immediately re-rendered the whole
// app tree (the context value changes on every message), which reflowed the
// dashboard and made the page stutter / fight the user's scroll. Flushing in
// batches caps that at ~1000/flushMs re-renders per second instead.
export function useWebSocket(max = 200, flushMs = 350) {
  const [events, setEvents] = useState([])
  const [count, setCount] = useState(0)
  const [connected, setConnected] = useState(false)
  const wsRef = useRef(null)
  const bufRef = useRef([])

  useEffect(() => {
    let alive = true
    let retry

    // Drain the buffer into state on a fixed interval — one render per flush,
    // no matter how many messages arrived in between.
    const flush = setInterval(() => {
      if (!bufRef.current.length) return
      const batch = bufRef.current
      bufRef.current = []
      setEvents((prev) => [...prev, ...batch].slice(-max))
      // Monotonic counter: effects keyed on it (e.g. the dashboard refresh) keep
      // firing during generation. Bump by the batch size, not by 1.
      setCount((c) => c + batch.length)
    }, flushMs)

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
          bufRef.current.push(msg)  // buffered; the interval flushes it
        } catch { /* ignore */ }
      }
    }
    connect()
    return () => {
      alive = false
      clearInterval(flush)
      clearTimeout(retry)
      wsRef.current?.close()
    }
  }, [max, flushMs])

  return { events, connected, count }
}
