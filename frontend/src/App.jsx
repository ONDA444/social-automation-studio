import { createContext, useContext } from 'react'
import { Routes, Route, Navigate } from 'react-router-dom'
import Sidebar from './components/Sidebar.jsx'
import TopBar from './components/TopBar.jsx'
import { useWebSocket } from './useWebSocket.js'

import Dashboard from './pages/Dashboard.jsx'
import Queue from './pages/Queue.jsx'
import Approvals from './pages/Approvals.jsx'
import RemixEngine from './pages/RemixEngine.jsx'
import Platforms from './pages/Platforms.jsx'
import Schedule from './pages/Schedule.jsx'
import Analytics from './pages/Analytics.jsx'
import Settings from './pages/Settings.jsx'

const WsContext = createContext({ events: [], connected: false })
export const useWs = () => useContext(WsContext)

export default function App() {
  const ws = useWebSocket(250)
  return (
    <WsContext.Provider value={ws}>
      <div className="flex h-screen overflow-hidden">
        <Sidebar />
        <div className="flex-1 flex flex-col min-w-0">
          <TopBar connected={ws.connected} />
          <main className="flex-1 overflow-y-auto p-6">
            <Routes>
              <Route path="/" element={<Dashboard />} />
              <Route path="/queue" element={<Queue />} />
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
