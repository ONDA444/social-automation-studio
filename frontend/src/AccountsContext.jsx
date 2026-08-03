import { createContext, useCallback, useContext, useEffect, useRef, useState } from 'react'
import { api } from './api'

// Shared GET /accounts cache. Before this, Queue/Schedule/Analytics/RemixEngine/
// AddJobModal/Platforms each ran their own useState([]) + useEffect fetch, so
// switching between pages re-fired the same request and left every other
// mounted screen's copy stale (e.g. connecting a channel in Platforms.jsx never
// showed up in Queue.jsx until it happened to refetch on its own). Provider
// lives above the router in App.jsx, so the list — and any refresh() — is
// shared across navigations instead of being refetched from scratch per page.
const AccountsContext = createContext({ accounts: [], loading: true, error: false, refresh: () => Promise.resolve([]) })

export function AccountsProvider({ children }) {
  const [accounts, setAccounts] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(false)
  const accountsRef = useRef(accounts)
  accountsRef.current = accounts

  // Never rejects — on failure it keeps whatever was last loaded successfully
  // (stale-while-revalidate) instead of blanking out every page that reads
  // from this cache over one transient network hiccup. `error` still flips so
  // a page that wants to warn the user (e.g. an "offline" banner) still can.
  const refresh = useCallback((opts) => (
    api.get('/accounts', opts)
      .then((d) => { const a = d.accounts || []; setAccounts(a); setError(false); return a })
      .catch(() => { setError(true); return accountsRef.current })
      .finally(() => setLoading(false))
  ), [])

  useEffect(() => { refresh() }, [refresh])

  return (
    <AccountsContext.Provider value={{ accounts, loading, error, refresh }}>
      {children}
    </AccountsContext.Provider>
  )
}

export const useAccounts = () => useContext(AccountsContext)
