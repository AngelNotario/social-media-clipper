import { useEffect, useState } from 'react'
import Icon from '../components/Icon.jsx'
import { api, fmtDuration, fmtSize } from '../api.js'

export default function Dashboard({ navigate }) {
  const [st, setSt] = useState(null)
  const [err, setErr] = useState(null)

  useEffect(() => {
    api.status().then(setSt).catch((e) => setErr(e.message))
  }, [])

  if (err) return <div className="page"><div className="alert alert-error">No se pudo conectar con el backend: {err}</div></div>
  if (!st) return <div className="page"><div className="muted">Cargando…</div></div>

  const lib = st.library
  return (
    <div className="page">
      <header className="page-head">
        <div>
          <h1>Dashboard</h1>
          <p className="muted">Resumen de tu biblioteca y del estado del sistema.</p>
        </div>
        <button className="btn btn-primary" onClick={() => navigate('explorer')}>
          <Icon name="plus" /> Importar videos
        </button>
      </header>

      {!st.ffmpeg.available && (
        <div className="alert alert-error">FFmpeg no está disponible. La importación no funcionará hasta instalarlo y reiniciar la app.</div>
      )}
      {st.authorized_folders.length === 0 && (
        <div className="alert alert-warn">
          No hay carpetas autorizadas. <a onClick={() => navigate('settings')}>Agrega una en Configuración</a>.
        </div>
      )}

      <section className="stats">
        <Stat label="Videos" value={lib.videos} icon="film" />
        <Stat label="Duración total" value={fmtDuration(lib.total_duration)} icon="play" />
        <Stat label="Espacio" value={fmtSize(lib.total_size)} icon="folder" />
        <Stat label="Vertical / Horizontal" value={`${lib.vertical} / ${lib.horizontal}`} icon="grid" />
      </section>

      <section className="panel">
        <div className="panel-head">
          <h2>Importados recientemente</h2>
          {lib.videos > 0 && <a onClick={() => navigate('library')}>Ver biblioteca →</a>}
        </div>
        {st.recent.length === 0 ? (
          <div className="empty">
            <Icon name="film" size={32} />
            <p>Tu biblioteca está vacía.</p>
            <button className="btn" onClick={() => navigate('explorer')}>Explorar mis carpetas</button>
          </div>
        ) : (
          <div className="recent">
            {st.recent.map((v) => (
              <button key={v.id} className="recent-item" onClick={() => navigate('library')}>
                {v.thumbnail_url ? <img src={v.thumbnail_url} alt="" /> : <div className="vcard-nothumb"><Icon name="film" /></div>}
                <div>
                  <div className="recent-title">{v.title}</div>
                  <div className="muted small">{fmtDuration(v.duration)} · {v.width}×{v.height}</div>
                </div>
              </button>
            ))}
          </div>
        )}
      </section>

      <section className="panel">
        <div className="panel-head"><h2>Sistema</h2></div>
        <dl className="meta meta-wide">
          <dt>FFmpeg</dt>
          <dd>{st.ffmpeg.available ? <span className="ok">● {st.ffmpeg.version}</span> : <span className="bad">● No encontrado</span>}</dd>
          <dt>Carpetas autorizadas</dt>
          <dd>{st.authorized_folders.length ? st.authorized_folders.join(' · ') : '—'}</dd>
          <dt>Datos de la app</dt>
          <dd><code>{st.data_dir}</code></dd>
        </dl>
      </section>
    </div>
  )
}

function Stat({ label, value, icon }) {
  return (
    <div className="stat">
      <div className="stat-icon"><Icon name={icon} /></div>
      <div>
        <div className="stat-value">{value}</div>
        <div className="stat-label">{label}</div>
      </div>
    </div>
  )
}
