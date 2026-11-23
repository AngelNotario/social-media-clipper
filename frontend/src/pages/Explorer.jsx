import { useCallback, useEffect, useState } from 'react'
import Icon from '../components/Icon.jsx'
import { api, fmtDate, fmtSize } from '../api.js'

const q = (p) => encodeURIComponent(p)
const thumbUrl = (f) => `/api/browse/thumb?path=${q(f.path)}&v=${Math.round(f.modified)}`
const streamUrl = (f) => `/api/browse/stream?path=${q(f.path)}`

function Thumb({ f, onOpen }) {
  const [state, setState] = useState('loading')
  return (
    <button type="button" className={`ex-thumb ${state}`} title="Ver video"
      onClick={(e) => { e.stopPropagation(); onOpen(f) }}>
      {state !== 'error' && <img src={thumbUrl(f)} alt="" loading="lazy" decoding="async"
        onLoad={() => setState('ok')} onError={() => setState('error')} />}
      {state === 'error' && <Icon name="film" />}
      <span className="ex-play"><Icon name="play" size={14} /></span>
    </button>
  )
}

function Preview({ f, selected, onToggle, onClose, onPrev, onNext }) {
  const [err, setErr] = useState(false)
  useEffect(() => setErr(false), [f.path])
  useEffect(() => {
    const k = (e) => {
      if (e.key === 'Escape') onClose()
      else if (e.key === 'ArrowDown' || e.key === 'PageDown') { e.preventDefault(); onNext() }
      else if (e.key === 'ArrowUp' || e.key === 'PageUp') { e.preventDefault(); onPrev() }
    }
    window.addEventListener('keydown', k)
    return () => window.removeEventListener('keydown', k)
  }, [onClose, onPrev, onNext])
  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="ex-preview" onClick={(e) => e.stopPropagation()}>
        <button className="icon-btn modal-close" title="Cerrar (Esc)" onClick={onClose}><Icon name="x" /></button>
        <div className="ex-preview-video">
          {err ? (
            <div className="player-msg">El navegador no puede reproducir este archivo (puede ser el códec).<br />La miniatura sí te sirve para reconocerlo.<br /><img src={thumbUrl(f)} alt="" /></div>
          ) : (
            <video key={f.path} src={streamUrl(f)} poster={thumbUrl(f)} controls autoPlay preload="metadata" onError={() => setErr(true)} />
          )}
        </div>
        <div className="ex-preview-bar">
          <button className="icon-btn" title="Anterior (↑)" onClick={onPrev}><Icon name="prev" /></button>
          <div className="ex-preview-info">
            <b>{f.name}</b>
            <span className="small muted">{fmtSize(f.size_bytes)} · {fmtDate(f.modified)}</span>
          </div>
          <button className="icon-btn" title="Siguiente (↓)" onClick={onNext}><Icon name="next" /></button>
          {f.imported_id
            ? <span className="badge badge-ok"><Icon name="check" size={12} /> Importado</span>
            : <button className={`btn ${selected ? '' : 'btn-primary'}`} onClick={() => onToggle(f.path)}>
                {selected ? 'Quitar de la selección' : 'Seleccionar para importar'}
              </button>}
        </div>
      </div>
    </div>
  )
}

export default function Explorer({ navigate, notify }) {
  const [path, setPath] = useState(null)
  const [data, setData] = useState(null)
  const [err, setErr] = useState(null)
  const [selected, setSelected] = useState(new Set())
  const [mode, setMode] = useState('reference')
  const [busy, setBusy] = useState(false)
  const [preview, setPreview] = useState(null)

  const load = useCallback((p) => {
    setErr(null)
    api.browse(p)
      .then((d) => { setData(d); setSelected(new Set()) })
      .catch((e) => setErr(e.message))
  }, [])

  useEffect(() => { load(path) }, [path, load])

  const toggle = (p) => {
    const s = new Set(selected)
    s.has(p) ? s.delete(p) : s.add(p)
    setSelected(s)
  }
  const files = data?.files || []
  const pIdx = preview ? files.findIndex((f) => f.path === preview.path) : -1
  const step = (d) => { if (files.length) setPreview(files[(pIdx + d + files.length) % files.length]) }
  const importable = (data?.files || []).filter((f) => !f.imported_id)
  const allSelected = importable.length > 0 && importable.every((f) => selected.has(f.path))
  const toggleAll = () => setSelected(allSelected ? new Set() : new Set(importable.map((f) => f.path)))

  const doImport = async () => {
    setBusy(true)
    try {
      const r = await api.importVideos([...selected], mode)
      const fails = r.results.filter((x) => !x.ok)
      if (r.imported) notify(`${r.imported} video${r.imported === 1 ? '' : 's'} importado${r.imported === 1 ? '' : 's'}`)
      if (fails.length) notify(`${fails.length} con error: ${fails[0].error}`, 'error')
      load(path)
    } catch (e) {
      notify(e.message, 'error')
    } finally {
      setBusy(false)
    }
  }

  // Migas de pan relativas a la carpeta autorizada
  const crumbs = []
  if (data?.path && data.root) {
    const sep = data.path.includes('\\') ? '\\' : '/'
    const rel = data.path.slice(data.root.length).split(/[\\/]/).filter(Boolean)
    crumbs.push({ label: data.root.split(/[\\/]/).filter(Boolean).pop() || data.root, path: data.root })
    let acc = data.root.replace(/[\\/]$/, '')
    rel.forEach((part) => { acc = `${acc}${sep}${part}`; crumbs.push({ label: part, path: acc }) })
  }

  return (
    <div className="page">
      <header className="page-head">
        <div>
          <h1>Explorar archivos</h1>
          <p className="muted">Solo se muestran tus carpetas autorizadas y los archivos de video.</p>
        </div>
      </header>

      <div className="explorer-bar">
        <button className="icon-btn" title="Carpetas autorizadas" onClick={() => setPath(null)}><Icon name="home" /></button>
        <button className="icon-btn" title="Subir un nivel" disabled={!data?.parent} onClick={() => setPath(data.parent)}><Icon name="up" /></button>
        <button className="icon-btn" title="Recargar" onClick={() => load(path)}><Icon name="refresh" /></button>
        <div className="crumbs">
          {crumbs.length === 0 ? <span>Carpetas autorizadas</span> : crumbs.map((c, i) => (
            <span key={c.path}>
              {i > 0 && <span className="crumb-sep">/</span>}
              <a onClick={() => setPath(c.path)}>{c.label}</a>
            </span>
          ))}
        </div>
      </div>

      {err && <div className="alert alert-error">{err}</div>}

      {data && data.path === null && (
        <div className="panel">
          {data.roots.length === 0 ? (
            <div className="empty">
              <Icon name="folder" size={32} />
              <p>No hay carpetas autorizadas todavía.</p>
              <button className="btn" onClick={() => navigate('settings')}>Agregar carpeta</button>
            </div>
          ) : (
            <ul className="filelist">
              {data.roots.map((r) => (
                <li key={r.path} className={`file-row dir ${r.exists ? '' : 'missing'}`} onClick={() => r.exists && setPath(r.path)}>
                  <Icon name="folder" /> <span className="fname">{r.name}</span>
                  <span className="muted small">{r.exists ? r.path : 'No existe'}</span>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}

      {data && data.path !== null && (
        <>
          <div className="panel">
            <ul className="filelist">
              {data.dirs.map((d) => (
                <li key={d.path} className="file-row dir" onClick={() => setPath(d.path)}>
                  <Icon name="folder" /> <span className="fname">{d.name}</span>
                </li>
              ))}
              {data.files.length > 0 && (
                <li className="file-row head">
                  <input type="checkbox" checked={allSelected} onChange={toggleAll} disabled={!importable.length} />
                  <span /><span className="fname">Archivo</span><span className="small muted">Tamaño</span><span className="small muted">Modificado</span><span />
                </li>
              )}
              {data.files.map((f) => (
                <li key={f.path} className={`file-row file ${selected.has(f.path) ? 'sel' : ''}`}
                  onClick={() => !f.imported_id && toggle(f.path)}>
                  <input type="checkbox" checked={selected.has(f.path)} disabled={!!f.imported_id} readOnly />
                  <Thumb f={f} onOpen={setPreview} />
                  <span className="fname">{f.name}</span>
                  <span className="small muted">{fmtSize(f.size_bytes)}</span>
                  <span className="small muted">{fmtDate(f.modified)}</span>
                  <span>{f.imported_id && <span className="badge badge-ok"><Icon name="check" size={12} /> Importado</span>}</span>
                </li>
              ))}
              {data.dirs.length === 0 && data.files.length === 0 && (
                <li className="empty">Esta carpeta no tiene subcarpetas ni videos.</li>
              )}
            </ul>
          </div>

          {preview && (
            <Preview f={preview} selected={selected.has(preview.path)} onToggle={toggle}
              onClose={() => setPreview(null)} onPrev={() => step(-1)} onNext={() => step(1)} />
          )}

          {selected.size > 0 && (
            <div className="import-bar">
              <span><b>{selected.size}</b> seleccionado{selected.size === 1 ? '' : 's'}</span>
              <div className="seg">
                <button className={mode === 'reference' ? 'on' : ''} onClick={() => setMode('reference')} title="No duplica el archivo: la app lo lee desde su ubicación">Usar original</button>
                <button className={mode === 'copy' ? 'on' : ''} onClick={() => setMode('copy')} title="Copia el archivo a la carpeta data/media de la app">Copiar a la app</button>
              </div>
              <button className="btn btn-primary" onClick={doImport} disabled={busy}>
                {busy ? 'Importando…' : 'Importar a la biblioteca'}
              </button>
            </div>
          )}
        </>
      )}
    </div>
  )
}
