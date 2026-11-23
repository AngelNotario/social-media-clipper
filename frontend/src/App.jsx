import { useCallback, useEffect, useState } from 'react'
import Sidebar from './components/Sidebar.jsx'
import Dashboard from './pages/Dashboard.jsx'
import Library from './pages/Library.jsx'
import Explorer from './pages/Explorer.jsx'
import Settings from './pages/Settings.jsx'
import Clips from './pages/Clips.jsx'
import Exports from './pages/Exports.jsx'
import Studio from './pages/Studio.jsx'
import Stream from './pages/Stream.jsx'
import Publish from './pages/Publish.jsx'
import Thumbs from './pages/Thumbs.jsx'
import Resources from './pages/Resources.jsx'

const PAGES = {
  dashboard: Dashboard,
  library: Library,
  explorer: Explorer,
  studio: Studio,
  stream: Stream,
  clips: Clips,
  exports: Exports,
  publish: Publish,
  thumbs: Thumbs,
  resources: Resources,
  settings: Settings,
}

function currentPage() {
  const h = window.location.hash.replace('#/', '').split('?')[0]
  return PAGES[h] ? h : 'dashboard'
}

export default function App() {
  const [page, setPage] = useState(currentPage)
  const [hash, setHash] = useState(window.location.hash)
  const [toast, setToast] = useState(null)

  useEffect(() => {
    const onHash = () => { setPage(currentPage()); setHash(window.location.hash) }
    window.addEventListener('hashchange', onHash)
    return () => window.removeEventListener('hashchange', onHash)
  }, [])

  useEffect(() => {
    if (!toast) return
    const t = setTimeout(() => setToast(null), 4500)
    return () => clearTimeout(t)
  }, [toast])

  const navigate = useCallback((p) => { window.location.hash = `#/${p}` }, [])
  const notify = useCallback((text, kind = 'ok') => setToast({ text, kind }), [])
  const Page = PAGES[page]

  return (
    <div className="app">
      <Sidebar page={page} navigate={navigate} />
      <main className="main">
        <Page navigate={navigate} notify={notify} hash={hash} />
      </main>
      {toast && <div className={`toast toast-${toast.kind}`}>{toast.text}</div>}
    </div>
  )
}
