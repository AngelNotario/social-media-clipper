import { useEffect, useState } from 'react'
import Icon from './Icon.jsx'
import { api, fmtDuration, fmtSize } from '../api.js'

export default function PlayerModal({ video, onClose, onChanged, notify, navigate }) {
  const [title, setTitle] = useState(video.title)
  const [playError, setPlayError] = useState(false)

  useEffect(() => {
    const onKey = (e) => e.key === 'Escape' && onClose()
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  const save = async () => {
    try {
      await api.rename(video.id, title)
      notify('Título actualizado')
      onChanged()
    } catch (e) { notify(e.message, 'error') }
  }

  const remove = async () => {
    const extra = video.storage_mode === 'copy'
      ? ' Se borrará la copia interna; tu archivo original no se toca.'
      : ' Tu archivo original no se borra.'
    if (!window.confirm(`¿Quitar "${video.title}" de la biblioteca?${extra}`)) return
    try {
      await api.remove(video.id)
      notify('Video quitado de la biblioteca')
      onChanged()
      onClose()
    } catch (e) { notify(e.message, 'error') }
  }

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <button className="icon-btn modal-close" onClick={onClose} aria-label="Cerrar"><Icon name="x" /></button>
        <div className={`player-wrap ${video.orientation === 'vertical' ? 'is-vertical' : ''}`}>
          {!video.available ? (
            <div className="player-msg">El archivo ya no está en su ubicación original:<br /><code>{video.stored_path}</code></div>
          ) : playError ? (
            <div className="player-msg">
              El navegador no puede reproducir este formato ({video.video_codec || 'desconocido'}).<br />
              El video sí está en la biblioteca; la conversión llegará en el módulo de procesamiento.
            </div>
          ) : (
            <video src={video.stream_url} controls autoPlay onError={() => setPlayError(true)} />
          )}
        </div>
        <div className="modal-side">
          <label className="field-label">Título</label>
          <div className="row">
            <input className="input" value={title} onChange={(e) => setTitle(e.target.value)} />
            <button className="btn" onClick={save} disabled={!title.trim() || title === video.title}>Guardar</button>
          </div>
          <dl className="meta">
            <dt>Duración</dt><dd>{fmtDuration(video.duration)}</dd>
            <dt>Resolución</dt><dd>{video.width}×{video.height} ({video.orientation})</dd>
            <dt>FPS</dt><dd>{video.fps ?? '—'}</dd>
            <dt>Video</dt><dd>{video.video_codec ?? '—'}</dd>
            <dt>Audio</dt><dd>{video.has_audio ? video.audio_codec : 'Sin audio'}</dd>
            <dt>Tamaño</dt><dd>{fmtSize(video.size_bytes)}</dd>
            <dt>Almacenamiento</dt><dd>{video.storage_mode === 'copy' ? 'Copia en la app' : 'Referencia al original'}</dd>
            <dt>Importado</dt><dd>{video.imported_at}</dd>
          </dl>
          <div className="path" title={video.original_path}>{video.original_path}</div>
          {navigate && video.has_audio && (
            <button className="btn btn-primary" onClick={() => navigate(`clips?video=${video.id}`)}>
              <Icon name="spark" /> Crear clips con IA
            </button>
          )}
          <button className="btn btn-danger" onClick={remove}><Icon name="trash" /> Quitar de la biblioteca</button>
        </div>
      </div>
    </div>
  )
}
