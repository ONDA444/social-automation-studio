import { createContext, useContext, useState, useEffect } from 'react'
import { Routes, Route, Navigate } from 'react-router-dom'
import Lenis from 'lenis'
import Sidebar from './components/Sidebar.jsx'
import TopBar from './components/TopBar.jsx'
import { useWebSocket } from './useWebSocket.js'

import Dashboard from './pages/Dashboard.jsx'
import Queue from './pages/Queue.jsx'
import Shorts from './pages/Shorts.jsx'
import Approvals from './pages/Approvals.jsx'
import RemixEngine from './pages/RemixEngine.jsx'
import Platforms from './pages/Platforms.jsx'
import Schedule from './pages/Schedule.jsx'
import Analytics from './pages/Analytics.jsx'
import Settings from './pages/Settings.jsx'

const WsContext = createContext({ events: [], connected: false, count: 0 })
export const useWs = () => useContext(WsContext)

export default function App() {
  const ws = useWebSocket(250)
  const [mobileNavOpen, setMobileNavOpen] = useState(false)

  useEffect(() => {
    const lenis = new Lenis({
      duration: 1.2,
      easing: (t) => Math.min(1, 1.001 - Math.pow(2, -10 * t)),
      smooth: true,
    })
    const raf = (time) => { lenis.raf(time); requestAnimationFrame(raf) }
    requestAnimationFrame(raf)
    return () => lenis.destroy()
  }, [])

  return (
    <WsContext.Provider value={ws}>
      <div className="flex h-screen overflow-hidden">
        <Sidebar mobileOpen={mobileNavOpen} onClose={() => setMobileNavOpen(false)} />
        <div className="flex-1 flex flex-col min-w-0">
          <TopBar connected={ws.connected} onMenuClick={() => setMobileNavOpen(true)} />
          <main className="flex-1 overflow-y-auto p-6">
            <Routes>
              <Route path="/" element={<Dashboard />} />
              <Route path="/queue" element={<Queue />} />
              <Route path="/shorts" element={<Shorts />} />
              <Route path="/approvals" element={<Approvals />} />
              <Route path="/remix" element={<RemixEngine />} />
              <Route path="/platforms" element={<Platforms />} />
              <Route path="/schedule" element={<Schedule />} />
              <Route path="/analytics" element={<Analytics />} />
              <Route path="/settings" element={<Settings />} />
              <Route path="*" element={<Navigate to="/" replace />} />
            </Routes>
          </main>
        </div>
      </div>
    </WsContext.Provider>
  )
}
