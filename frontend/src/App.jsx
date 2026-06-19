import { createContext, useContext, useState, lazy, Suspense } from 'react'
import { Routes, Route, Navigate } from 'react-router-dom'
import Sidebar from './components/Sidebar.jsx'
import TopBar from './components/TopBar.jsx'
import { useWebSocket } from './useWebSocket.js'

// Dashboard is the landing route — keep it eager for instant first paint.
import Dashboard from './pages/Dashboard.jsx'
// Everything else is code-split: each page (and its heavy deps — recharts on
// Analytics, dnd-kit on Queue) loads only when you navigate to it, instead of
// shipping one 700KB bundle on first load.
const Queue = lazy(() => import('./pages/Queue.jsx'))
const Shorts = lazy(() => import('./pages/Shorts.jsx'))
const Approvals = lazy(() => import('./pages/Approvals.jsx'))
const RemixEngine = lazy(() => import('./pages/RemixEngine.jsx'))
const Platforms = lazy(() => import('./pages/Platforms.jsx'))
const Schedule = lazy(() => import('./pages/Schedule.jsx'))
const Analytics = lazy(() => import('./pages/Analytics.jsx'))
const Settings = lazy(() => import('./pages/Settings.jsx'))

function RouteFallback() {
  return (
    <div className="space-y-4">
      <div className="h-8 w-48 skeleton rounded-xl" />
      <div className="h-64 skeleton rounded-card" />
    </div>
  )
}

const WsContext = createContext({ events: [], connected: false, count: 0 })
export const useWs = () => useContext(WsContext)

export default function App() {
  const ws = useWebSocket(250)
  const [mobileNavOpen, setMobileNavOpen] = useState(false)

  return (
    <WsContext.Provider value={ws}>
      <div className="flex h-screen overflow-hidden">
        <Sidebar mobileOpen={mobileNavOpen} onClose={() => setMobileNavOpen(false)} />
        <div className="flex-1 flex flex-col min-w-0">
          <TopBar connected={ws.connected} onMenuClick={() => setMobileNavOpen(true)} />
          <main className="flex-1 overflow-y-auto p-4 sm:p-6">
            <Suspense fallback={<RouteFallback />}>
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
            </Suspense>
          </main>
        </div>
      </div>
    </WsContext.Provider>
  )
}
