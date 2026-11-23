import { useEffect, useState } from 'react'

// Índice de recursos que la IA encontró en internet (logos, capturas, productos, clips).
// Se reutilizan en otras ediciones; aquí puedes buscarlos y quitar los que no te sirvan.
export default function Resources({ notify }) {
  const [items, setItems] = useState([])
  const [q, setQ] = useState('')
  const [loading, setLoading] = useState(true)

  const load = async (text = q) => {
    setLoading(true)
    try {
      const url = text.trim() ? `/api/resources/search?q=${encodeURIComponent(text.trim())}&limit=100` : '/api/resources?limit=300'
      setItems((await fetch(url).then((r) => r.json())).items || [])
    } catch (e) { notify?.(e.message, 'error') }
    setLoading(false)
  }
  useEffect(() => { load('') }, []) // eslint-disable-line react-hooks/exhaustive-deps

  const del = async (id) => {
    if (!window.confirm('¿Quitar este recurso del índice? Se borra el archivo descargado.')) return
    const r = await fetch(`/api/resources/${id}`, { method: 'DELETE' })
    if (r.ok) setItems((xs) => xs.filter((x) => x.id !== id))
    else notify?.('No se pudo quitar', 'error')
  }

  return (
    <div className="page">
      <h1>Recursos encontrados</h1>
      <p className="muted">Lo que la IA buscó y verificó en internet para tus ediciones. Se guarda aquí para reutilizarlo en
        otros videos. Revisa los derechos de uso antes de publicar (cada recurso muestra su fuente).</p>
      <div className="row gap mt">
        <input className="input" placeholder="Buscar (ej. Eneba, GTA 6, Xbox)" value={q} onChange={(e) => setQ(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && load()} />
        <button className="btn" onClick={() => load()}>Buscar</button>
        <span className="muted small">{loading ? 'Cargando…' : `${items.length} recursos`}</span>
      </div>
      <div className="res-grid mt">
        {items.map((r) => (
          <div key={r.id} className="res-card">
            <div className="res-media">
              {r.media_type === 'video'
                ? <video src={`/api/resources/${r.id}/file`} muted loop onMouseEnter={(e) => e.target.play()} onMouseLeave={(e) => e.target.pause()} />
                : <img src={`/api/resources/${r.id}/file`} alt={r.entity} loading="lazy" />}
            </div>
            <div className="res-info">
              <b>{r.entity}</b> <span className="badge">{r.what}</span>
              {r.verified_score != null && <span className="badge badge-ok" title="Verificado con visión">✓ {r.verified_score}</span>}
              <div className="muted small">Usado {r.used_count || 0} veces{r.page_url ? ' · ' : ''}
                {r.page_url && <a href={r.page_url} target="_blank" rel="noreferrer">fuente</a>}</div>
              <button className="btn sm" onClick={() => del(r.id)}>Quitar</button>
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}
