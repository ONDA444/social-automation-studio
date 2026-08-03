import { createContext, useContext, useEffect, useState, lazy, Suspense } from 'react'
import { Routes, Route, Navigate } from 'react-router-dom'
import Privacy from './pages/Privacy.jsx'
import Terms from './pages/Terms.jsx'
import Sidebar from './components/Sidebar.jsx'
import TopBar from './components/TopBar.jsx'
import ErrorBoundary from './components/ErrorBoundary.jsx'
import { ToastProvider, ConfirmProvider } from './components/ui.jsx'
import { useWebSocket } from './useWebSocket.js'
import { useDashboardData } from './useDashboardData.js'
import { AccountsProvider } from './AccountsContext.jsx'

// Dashboard is the landing route — keep it eager for instant first paint.
import Dashboard from './pages/Dashboard.jsx'
// Everything else is code-split: each page (and its heavy deps — recharts on
// Analytics) loads only when you navigate to it, instead of shipping one
// 700KB bundle on first load.
const Queue = lazy(() => import('./pages/Queue.jsx'))
const Shorts = lazy(() => import('./pages/Shorts.jsx'))
const Approvals = lazy(() => import('./pages/Approvals.jsx'))
const RemixEngine = lazy(() => import('./pages/RemixEngine.jsx'))
const Platforms = lazy(() => import('./pages/Platforms.jsx'))
const Channels = lazy(() => import('./pages/Channels.jsx'))
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

const DashboardStatusContext = createContext({ data: null, health: 'green', stale: false, refresh: () => {} })
export const useDashboardStatus = () => useContext(DashboardStatusContext)

export default function App() {
  const ws = useWebSocket(250)
  const [mobileNavOpen, setMobileNavOpen] = useState(false)
  const [theme, setTheme] = useState(() => localStorage.getItem('studio-theme') || 'dark')

  useEffect(() => {
    document.documentElement.dataset.theme = theme
    document.documentElement.style.colorScheme = theme
    localStorage.setItem('studio-theme', theme)
  }, [theme])

  const toggleTheme = () => setTheme((value) => value === 'dark' ? 'light' : 'dark')

  return (
    <ToastProvider>
      <ConfirmProvider>
        <WsContext.Provider value={ws}>
          <Routes>
            <Route path="/privacy" element={<Privacy />} />
            <Route path="/terms" element={<Terms />} />
            <Route path="*" element={<AppLayout ws={ws} theme={theme} toggleTheme={toggleTheme} mobileNavOpen={mobileNavOpen} setMobileNavOpen={setMobileNavOpen} />} />
          </Routes>
        </WsContext.Provider>
      </ConfirmProvider>
    </ToastProvider>
  )
}

function AppLayout({ ws, theme, toggleTheme, mobileNavOpen, setMobileNavOpen }) {
  // Mounted once here (not per-page), so the /dashboard + /dashboard/health
  // poll it drives runs a single time no matter the route, and the Dashboard
  // page reuses the same data instead of fetching it again on its own.
  const dashboardStatus = useDashboardData(ws.count)

  return (
    <AccountsProvider>
      <DashboardStatusContext.Provider value={dashboardStatus}>
        <div className="flex h-screen overflow-hidden">
          <Sidebar mobileOpen={mobileNavOpen} onClose={() => setMobileNavOpen(false)} />
          <div className="flex-1 flex flex-col min-w-0">
            <TopBar
              connected={ws.connected}
              onMenuClick={() => setMobileNavOpen(true)}
              theme={theme}
              onToggleTheme={toggleTheme}
            />

            <main className="flex-1 overflow-y-auto p-4 sm:p-6">
              <ErrorBoundary>
              <Suspense fallback={<RouteFallback />}>
                <Routes>
                  <Route path="/" element={<Dashboard />} />
                  <Route path="/queue" element={<Queue />} />
                  <Route path="/shorts" element={<Shorts />} />
                  <Route path="/approvals" element={<Approvals />} />
                  <Route path="/remix" element={<RemixEngine />} />
                  <Route path="/platforms" element={<Platforms />} />
                  <Route path="/channels" element={<Channels />} />
                  <Route path="/schedule" element={<Schedule />} />
                  <Route path="/analytics" element={<Analytics />} />
                  <Route path="/settings" element={<Settings />} />
                  <Route path="*" element={<Navigate to="/" replace />} />
                </Routes>
              </Suspense>
              </ErrorBoundary>
            </main>
          </div>
        </div>
      </DashboardStatusContext.Provider>
    </AccountsProvider>
  )
}
