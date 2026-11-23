import { useCallback, useEffect, useState } from 'react'
import Icon from '../components/Icon.jsx'
import VideoCard from '../components/VideoCard.jsx'
import PlayerModal from '../components/PlayerModal.jsx'
import { api } from '../api.js'

export default function Library({ navigate, notify }) {
  const [items, setItems] = useState(null)
  const [q, setQ] = useState('')
  const [orientation, setOrientation] = useState('')
  const [sort, setSort] = useState('recent')
  const [open, setOpen] = useState(null)

  const [info, setInfo] = useState(null)
  const load = useCallback((quiet) => {
    api.library({ q, orientation, sort })
      .then((d) => { setItems(d.items); setInfo(d) })
      .catch((e) => { if (!quiet) notify(e.message, 'error') })
  }, [q, orientation, sort, notify])

  useEffect(() => {
    const t = setTimeout(load, 200)
    return () => clearTimeout(t)
  }, [load])

  // En tiempo real: refleja lo que hay en tus carpetas (videos nuevos aparecen, los borrados se van)
  useEffect(() => {
    const tick = () => { if (document.visibilityState === 'visible') load(true) }
    const iv = setInterval(tick, 5000)
    window.addEventListener('focus', tick)
    return () => { clearInterval(iv); window.removeEventListener('focus', tick) }
  }, [load])

  return (
    <div className="page">
      <header className="page-head">
        <div>
          <h1>Biblioteca de videos</h1>
          <p className="muted">{items ? `${items.length} video${items.length === 1 ? '' : 's'}` : 'Cargando…'}
            {info?.folders?.length ? ` · se actualiza solo con ${info.folders.join(', ')}` : ''}
            {info?.pending ? ` · agregando ${info.pending} nuevo${info.pending === 1 ? '' : 's'}…` : ''}</p>
        </div>
        <button className="btn btn-primary" onClick={() => navigate('explorer')}><Icon name="plus" /> Importar</button>
      </header>

      <div className="toolbar">
        <div className="search">
          <Icon name="search" />
          <input placeholder="Buscar por título…" value={q} onChange={(e) => setQ(e.target.value)} />
        </div>
        <div className="seg">
          {[['', 'Todos'], ['vertical', 'Vertical'], ['horizontal', 'Horizontal']].map(([v, l]) => (
            <button key={v} className={orientation === v ? 'on' : ''} onClick={() => setOrientation(v)}>{l}</button>
          ))}
        </div>
        <select className="input select" value={sort} onChange={(e) => setSort(e.target.value)}>
          <option value="recent">Más recientes</option>
          <option value="title">Título (A-Z)</option>
          <option value="duration">Duración</option>
        </select>
      </div>

      {items && items.length === 0 ? (
        <div className="empty panel">
          <Icon name="film" size={32} />
          <p>{q || orientation ? 'Ningún video coincide con el filtro.' : 'Aún no has importado videos.'}</p>
          {!q && !orientation && <button className="btn" onClick={() => navigate('explorer')}>Explorar mis carpetas</button>}
        </div>
      ) : (
        <div className="grid">
          {(items || []).map((v) => <VideoCard key={v.id} video={v} onClick={() => setOpen(v)} />)}
        </div>
      )}

      {open && (
        <PlayerModal video={open} notify={notify} navigate={navigate} onClose={() => setOpen(null)} onChanged={load} />
      )}
    </div>
  )
}
