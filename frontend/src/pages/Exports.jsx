import { useCallback, useEffect, useState } from 'react'
import Icon from '../components/Icon.jsx'
import PublishComposer from '../components/PublishComposer.jsx'
import { api, fmtDuration, fmtSize } from '../api.js'

// Dos listas separadas para no revolverlas: clips de «Clips con IA» y videos finales editados en el Studio.
export default function Exports({ navigate, notify }) {
  const [tab, setTab] = useState(() => (window.location.hash.includes('tab=studio') ? 'studio' : 'clips'))
  const [items, setItems] = useState(null)
  const [finals, setFinals] = useState(null)
  const [compose, setCompose] = useState(null)
  const [sending, setSending] = useState(null)

  const load = useCallback(() => {
    api.renders().then(setItems).catch((e) => notify(e.message, 'error'))
    fetch('/api/studio/finals').then((r) => r.json()).then(setFinals).catch((e) => notify(e.message, 'error'))
  }, [notify])
  useEffect(() => { load() }, [load])

  const remove = async (r) => {
    if (!window.confirm(`¿Eliminar el archivo "${r.file_name}"?`)) return
    try { await api.deleteRender(r.id); load(); notify('Exportación eliminada') } catch (e) { notify(e.message, 'error') }
  }
  const removeFinal = async (r) => {
    if (!window.confirm(`¿Eliminar el video "${r.file_name}"? El proyecto del Studio no se borra.`)) return
    const res = await fetch(`/api/studio/renders/${r.id}`, { method: 'DELETE' })
    if (res.ok) { load(); notify('Video eliminado') } else notify('No se pudo eliminar', 'error')
  }
  const toClips = async (r) => {
    setSending(r.id)
    try {
      const res = await fetch(`/api/studio/renders/${r.id}/to-library`, { method: 'POST' })
      const j = await res.json()
      if (!res.ok) throw new Error(j.detail || 'No se pudo enviar')
      navigate(`clips?video=${j.video_id}`)
    } catch (e) { notify(e.message, 'error') }
    setSending(null)
  }

  return (
    <div className="page">
      <header className="page-head">
        <div>
          <h1>Exportaciones</h1>
          <p className="muted">Clips de «Clips con IA» en <code>data\exports</code> · videos del Studio en <code>data\studio\renders</code></p>
        </div>
      </header>
      <div className="seg mb">
        <button className={tab === 'clips' ? 'on' : ''} onClick={() => setTab('clips')}>✂️ Clips con IA ({items?.length ?? '…'})</button>
        <button className={tab === 'studio' ? 'on' : ''} onClick={() => setTab('studio')}>🎬 Editados en Studio ({finals?.length ?? '…'})</button>
      </div>

      {tab === 'clips' && (items?.length === 0 ? (
        <div className="panel empty">
          <Icon name="upload" size={32} />
          <p>Aún no has generado clips.</p>
          <button className="btn" onClick={() => navigate('clips')}>Ir a Clips con IA</button>
        </div>
      ) : (
        <div className="export-grid">
          {(items || []).map((r) => (
            <div key={r.id} className="export-card">
              {r.available ? <video src={r.url} controls preload="metadata" /> : <div className="vcard-nothumb">No encontrado</div>}
              <div className="export-body">
                <div className="vcard-title" title={r.clip_title}>{r.clip_title}</div>
                <div className="muted small">{r.video_title}</div>
                <div className="muted small">{fmtDuration(r.duration)} · {fmtSize(r.size_bytes)} · {r.created_at}</div>
                <div className="row mt">
                  {r.available && <button className="btn sm btn-primary" onClick={() => setCompose({ kind: 'clip_render', id: r.id })}><Icon name="send" size={14} /> Publicar</button>}
                  {r.available && <a className="btn sm" href={`${r.url}?download=true`}><Icon name="upload" size={14} /> Descargar</a>}
                  <button className="btn sm" onClick={() => navigate(`clips?video=${r.video_id}`)}>Ver clip</button>
                  <button className="icon-btn sm" title="Eliminar" onClick={() => remove(r)}><Icon name="trash" size={14} /></button>
                </div>
              </div>
            </div>
          ))}
        </div>
      ))}

      {tab === 'studio' && (finals?.length === 0 ? (
        <div className="panel empty">
          <Icon name="upload" size={32} />
          <p>Aún no has exportado videos desde el Studio.</p>
          <button className="btn" onClick={() => navigate('studio')}>Ir al Studio</button>
        </div>
      ) : (
        <div className="export-grid">
          {(finals || []).map((r) => (
            <div key={r.id} className="export-card">
              {r.available ? <video src={r.url} controls preload="metadata" /> : <div className="vcard-nothumb">No encontrado</div>}
              <div className="export-body">
                <div className="vcard-title" title={r.title}>{r.title}</div>
                <div className="muted small">Editado en Studio{r.library_video_id ? ' · ya está en Clips con IA' : ''}</div>
                <div className="muted small">{fmtDuration(r.duration)} · {fmtSize(r.size_bytes)} · {r.created_at}</div>
                <div className="row mt">
                  {r.available && <button className="btn sm btn-primary" onClick={() => setCompose({ kind: 'studio_render', id: r.id })}><Icon name="send" size={14} /> Publicar</button>}
                  {r.available && <a className="btn sm" href={`${r.url}?download=true`}><Icon name="upload" size={14} /> Descargar</a>}
                  {r.available && <button className="btn sm" disabled={sending === r.id} onClick={() => toClips(r)}
                    title="Sacar clips verticales de este video ya editado">✂️ {sending === r.id ? 'Enviando…' : 'Hacer clips'}</button>}
                  <button className="btn sm" onClick={() => navigate(`studio?p=${r.project_id}`)}>Abrir proyecto</button>
                  <button className="icon-btn sm" title="Eliminar" onClick={() => removeFinal(r)}><Icon name="trash" size={14} /></button>
                </div>
              </div>
            </div>
          ))}
        </div>
      ))}
      {compose && <PublishComposer source={compose} notify={notify} navigate={navigate} onClose={() => setCompose(null)} />}
    </div>
  )
}
