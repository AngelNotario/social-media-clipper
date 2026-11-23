import { useCallback, useEffect, useRef, useState } from 'react'
import Icon from '../components/Icon.jsx'
import JobProgress from '../components/JobProgress.jsx'
import RolesPanel from '../components/RolesPanel.jsx'
import ModelPicker, { useModelChoice } from '../components/ModelPicker.jsx'
import Feedback from '../components/Feedback.jsx'
import { api, fmtDuration, getQuery } from '../api.js'
import { useJob } from '../hooks.js'

const req = async (path, opts = {}) => {
  const r = await fetch(`/api/stream${path}`, { headers: { 'Content-Type': 'application/json' }, ...opts })
  const d = await r.json().catch(() => null)
  if (!r.ok) throw new Error(typeof d?.detail === 'string' ? d.detail : `Error ${r.status}`)
  return d
}

const CAM_MODES = [
  ['auto', 'Híbrido: usa la cámara solo cuando está encendida'],
  ['always', 'Siempre con cámara'],
  ['never', 'Sin cámara (juego completo)'],
]

export default function Stream({ navigate, notify, hash }) {
  const [videos, setVideos] = useState(null)
  const videoId = Number(getQuery().video) || null
  useEffect(() => { api.videosOverview().then(setVideos).catch((e) => notify(e.message, 'error')) }, [notify, hash])
  useEffect(() => {  // en tiempo real: grabaciones nuevas aparecen y las borradas desaparecen
    const tick = () => { if (document.visibilityState === 'visible') api.videosOverview().then(setVideos).catch(() => {}) }
    const iv = setInterval(tick, 5000)
    window.addEventListener('focus', tick)
    return () => { clearInterval(iv); window.removeEventListener('focus', tick) }
  }, [])

  return (
    <div className="page page-wide">
      <header className="page-head">
        <div>
          <h1>Stream</h1>
          <p className="muted">Sube la grabación de tu stream: la app saca clips verticales (cámara arriba, juego abajo)
            y arma un video resumen para YouTube con capítulos.</p>
        </div>
      </header>
      <div className="clips-layout">
        <aside className="video-list panel">
          <div className="panel-head"><h2>Grabaciones</h2></div>
          {videos?.length === 0 && (
            <div className="empty small">
              <p>Importa la grabación de OBS primero.</p>
              <button className="btn" onClick={() => navigate('explorer')}>Explorar archivos</button>
            </div>
          )}
          {(videos || []).map((v) => (
            <button key={v.id} className={`vl-item ${v.id === videoId ? 'on' : ''}`}
              onClick={() => { window.location.hash = `#/stream?video=${v.id}` }}>
              {v.thumbnail_url ? <img src={v.thumbnail_url} alt="" /> : <div className="vl-nothumb"><Icon name="film" /></div>}
              <div className="vl-body">
                <div className="vl-title">{v.title}</div>
                <div className="vl-sub">{fmtDuration(v.duration)}{v.clips ? ` · ${v.clips} clips` : ''}</div>
              </div>
            </button>
          ))}
        </aside>
        <section className="clips-main">
          {videoId
            ? <StreamWorkspace key={videoId} videoId={videoId} notify={notify} navigate={navigate} />
            : <div className="panel empty"><Icon name="film" size={32} /><p>Elige la grabación de tu stream.</p></div>}
        </section>
      </div>
    </div>
  )
}

function StreamWorkspace({ videoId, notify }) {
  const [st, setSt] = useState(null)
  const load = useCallback(() => req(`/videos/${videoId}`).then(setSt).catch((e) => notify(e.message, 'error')),
    [videoId, notify])
  useEffect(() => { load() }, [load])

  const save = async (body) => {
    try { await req(`/videos/${videoId}`, { method: 'PUT', body: JSON.stringify(body) }); load() } catch (e) { notify(e.message, 'error') }
  }
  if (!st) return <div className="panel">Cargando…</div>

  return (<>
    <CameraPanel st={st} videoId={videoId} save={save} notify={notify} reload={load} />
    {st.audio_tracks >= 2 && (
      <div className="panel">
        <div className="panel-head"><h2>Audio</h2></div>
        <p className="muted small">Tu grabación tiene {st.audio_tracks} pistas de audio. Elige la de tu micrófono para que
          la IA entienda solo lo que dices tú (sin el juego ni las alertas).</p>
        <select className="input" value={st.voice_track ?? ''} onChange={(e) =>
          save({ voice_track: e.target.value === '' ? null : Number(e.target.value) })}>
          <option value="">Automático (pista {(st.auto_voice_track ?? 0) + 1})</option>
          <option value="-1">Usar la mezcla (pista 1)</option>
          {Array.from({ length: st.audio_tracks - 1 }, (_, i) => i + 1).map((k) =>
            <option key={k} value={k}>Pista {k + 1}</option>)}
        </select>
      </div>
    )}
    <ClipsPanel videoId={videoId} st={st} notify={notify} reload={load} />
    <YoutubePanel videoId={videoId} st={st} notify={notify} reload={load} />
  </>)
}

function CameraPanel({ st, videoId, save, notify, reload }) {
  const dur = st.video.duration || 0
  const [t, setT] = useState(Math.round(dur * 0.37))
  const [draft, setDraft] = useState(null)
  const [busy, setBusy] = useState(false)
  const box = useRef(null)
  const drag = useRef(null)
  const rect = draft || st.facecam

  const pos = (e) => {
    const r = box.current.getBoundingClientRect()
    return { x: Math.min(Math.max((e.clientX - r.left) / r.width, 0), 1), y: Math.min(Math.max((e.clientY - r.top) / r.height, 0), 1) }
  }
  const down = (e) => { e.preventDefault(); drag.current = pos(e); setDraft({ ...drag.current, w: 0, h: 0 }) }
  const move = (e) => {
    if (!drag.current) return
    const p = pos(e), a = drag.current
    setDraft({ x: Math.min(a.x, p.x), y: Math.min(a.y, p.y), w: Math.abs(p.x - a.x), h: Math.abs(p.y - a.y) })
  }
  const up = () => {
    if (!drag.current) return
    drag.current = null
    if (draft && draft.w > 0.03 && draft.h > 0.03) save({ facecam: draft })
    setDraft(null)
  }
  const detect = async () => {
    setBusy(true)
    try {
      const r = await req(`/videos/${videoId}/detect-cam`, { method: 'POST' })
      notify(r.found ? 'Cámara encontrada. Revisa el recuadro.' : 'No encontré tu cámara: márcala arrastrando sobre la imagen.',
        r.found ? 'ok' : 'error')
      reload()
    } catch (e) { notify(e.message, 'error') }
    setBusy(false)
  }

  return (
    <div className="panel">
      <div className="panel-head">
        <h2>Tu cámara</h2>
        <div className="row">
          <button className="btn sm" disabled={busy} onClick={detect}>{busy ? 'Buscando…' : 'Detectar sola'}</button>
          {st.facecam && <button className="btn sm" onClick={() => save({ clear_facecam: true })}>Quitar</button>}
        </div>
      </div>
      <p className="muted small">Arrastra sobre la imagen para marcar dónde está tu cámara. Si en el stream no usas
        cámara, deja el modo «Sin cámara».</p>
      <div ref={box} className="cam-frame" onMouseDown={down} onMouseMove={move} onMouseUp={up} onMouseLeave={up}>
        <img src={`/api/stream/videos/${videoId}/frame?t=${t}`} alt="" draggable={false} />
        {rect && rect.w > 0 && (
          <div className="cam-rect" style={{ left: `${rect.x * 100}%`, top: `${rect.y * 100}%`,
            width: `${rect.w * 100}%`, height: `${rect.h * 100}%` }}><span>Cámara</span></div>
        )}
      </div>
      <label className="field-label mt">Ver otro momento del stream</label>
      <input type="range" className="full" min={0} max={Math.max(1, Math.floor(dur - 1))} value={t}
        onChange={(e) => setT(Number(e.target.value))} />
      <label className="field-label mt">Cuando la cámara esté</label>
      <select className="input full" value={st.cam_mode} onChange={(e) => save({ cam_mode: e.target.value })}>
        {CAM_MODES.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
      </select>
    </div>
  )
}

function ClipsPanel({ videoId, st, notify, reload }) {
  const [opts, setOpts] = useState({ max_clips: 12, min_dur: 20, max_dur: 60 })
  const [model, setModel] = useModelChoice()
  const [jobId, setJobId] = useState(null)
  const job = useJob(jobId, (j) => {
    if (j.status === 'done') { notify(`Listo: ${j.result?.clips ?? ''} clips propuestos${j.result?.model ? ` con ${j.result.model}` : ''}.`); reload() }
  })
  const run = async () => {
    try { setJobId((await req(`/videos/${videoId}/clips`, { method: 'POST', body: JSON.stringify({ ...opts, llm_model: model || null }) })).id) }
    catch (e) { notify(e.message, 'error') }
  }
  const running = job && ['queued', 'running'].includes(job.status)
  return (
    <div className="panel">
      <div className="panel-head"><h2>Clips verticales (Shorts, TikTok, Reels)</h2></div>
      <p className="muted small">La IA busca los momentos con más emoción: gritos, risas, jugadas y lo que dices.
        Luego los revisas y generas cada clip con el encuadre «Stream».</p>
      <div className="row wrap">
        <label className="small">Máx. clips <input className="input sm" type="number" min={1} max={30} value={opts.max_clips}
          onChange={(e) => setOpts({ ...opts, max_clips: Number(e.target.value) })} /></label>
        <label className="small">Duración <input className="input sm" type="number" min={5} max={180} value={opts.min_dur}
          onChange={(e) => setOpts({ ...opts, min_dur: Number(e.target.value) })} /> a
          <input className="input sm" type="number" min={10} max={300} value={opts.max_dur}
            onChange={(e) => setOpts({ ...opts, max_dur: Number(e.target.value) })} /> s</label>
      </div>
      <div className="row mt wrap">
        <ModelPicker value={model} onChange={setModel} compact />
        <RolesPanel notify={notify} compact />
        <button className="btn btn-primary" disabled={running} onClick={run}><Icon name="spark" /> {st.clips > 0 ? 'Buscar de nuevo' : 'Buscar clips'}</button>
        {st.clips > 0 && <button className="btn" onClick={() => { window.location.hash = `#/clips?video=${videoId}` }}>
          Revisar {st.clips} clips</button>}
      </div>
      <JobProgress job={job} />
    </div>
  )
}

function YoutubePanel({ videoId, st, notify, reload }) {
  const [opts, setOpts] = useState(() => ({ target_min: 17, cold_open: true, captions: true,
    style_id: Number(localStorage.getItem('smc_yt_style')) || null }))
  const [styles, setStyles] = useState([])
  useEffect(() => { fetch('/api/studio/styles').then((r) => r.json()).then(setStyles).catch(() => {}) }, [])
  const [model, setModel] = useModelChoice()
  const [regenModel, setRegenModel] = useState({})
  const [jobId, setJobId] = useState(null)
  const job = useJob(jobId, (j) => {
    if (j.status === 'done') { notify(`Video para YouTube listo en el Studio${j.result?.model ? ` (${j.result.model})` : ''}.`); reload() }
  })
  const run = async (o = opts, m = model) => {
    try { setJobId((await req(`/videos/${videoId}/youtube`, { method: 'POST', body: JSON.stringify({ ...o, llm_model: m || null }) })).id) }
    catch (e) { notify(e.message, 'error') }
  }
  const running = job && ['queued', 'running'].includes(job.status)
  const copy = (txt) => navigator.clipboard?.writeText(txt).then(() => notify('Copiado.'))

  return (
    <div className="panel">
      <div className="panel-head"><h2>Video para YouTube (horizontal)</h2></div>
      <p className="muted small">Junta lo mejor del stream en orden, con un gancho de 10 s al inicio, transiciones,
        títulos de capítulo y subtítulos. Se abre en el Studio para que lo ajustes antes de exportar.</p>
      <label className="field-label mt">Estilo de edición</label>
      <select className="input full" value={opts.style_id || ''} onChange={(e) => {
        const v = e.target.value ? Number(e.target.value) : null
        setOpts({ ...opts, style_id: v }); try { localStorage.setItem('smc_yt_style', v || '') } catch { /* */ }
      }}>
        <option value="">Viral (predeterminado): cortes rápidos, zooms, memes y efectos</option>
        {styles.map((s) => <option key={s.id} value={s.id}>Estilo aprendido: {s.name}</option>)}
      </select>
      <p className="muted small">La IA copia el ritmo, subtítulos, transiciones y música del estilo, y lee su análisis antes de editar. Aprende estilos nuevos en Studio → Estilos aprendidos.</p>
      <label className="field-label mt">Duración: {opts.target_min} min</label>
      <input type="range" className="full" min={8} max={30} value={opts.target_min}
        onChange={(e) => setOpts({ ...opts, target_min: Number(e.target.value) })} />
      <label className="check"><input type="checkbox" checked={opts.cold_open}
        onChange={(e) => setOpts({ ...opts, cold_open: e.target.checked })} /> Gancho inicial con el mejor momento</label>
      <label className="check"><input type="checkbox" checked={opts.captions}
        onChange={(e) => setOpts({ ...opts, captions: e.target.checked })} /> Subtítulos</label>
      <div className="row mt wrap">
        <ModelPicker value={model} onChange={setModel} compact />
        <button className="btn btn-primary" disabled={running} onClick={() => run()}><Icon name="film" /> Crear video para YouTube</button>
      </div>
      <JobProgress job={job} />

      {st.youtube.map((y) => (
        <div key={y.project_id} className="yt-card">
          <div className="row between">
            <div>
              <strong>{y.name}</strong>
              <div className="muted small">Editado con {y.model ? <b>{y.model}</b> : y.model === null ? 'método sin IA' : 'IA (modelo predeterminado)'}
                {y.style ? <> · estilo <b>{y.style}</b></> : ''}
                {y.options?.target_min ? ` · objetivo ${y.options.target_min} min` : ''} · {y.updated_at}</div>
              {y.director && <div className="muted small">Director: {y.director.punch} golpes de zoom · {y.director.meme} memes · {y.director.callout} textos · {y.director.cam} zooms a tu cámara · {y.director.shake} sacudidas · {y.director.sfx} efectos</div>}
            </div>
            <button className="btn sm btn-primary" onClick={() => { window.location.hash = `#/studio?p=${y.project_id}` }}>
              Abrir en el Studio</button>
          </div>
          <div className="row wrap mt regen-row">
            <span className="small muted">Regenerar con otro modelo:</span>
            <ModelPicker value={regenModel[y.project_id] ?? ''} compact
              onChange={(m) => setRegenModel({ ...regenModel, [y.project_id]: m })} />
            <button className="btn sm" disabled={running}
              onClick={() => run({ ...(y.options || {}), style_id: opts.style_id }, regenModel[y.project_id] ?? '')}
              title="Usa el estilo elegido arriba">↻ Regenerar</button>
          </div>
          <Feedback kind="youtube" refId={y.project_id} notify={notify}
            context={{ title: y.name, model: y.model, style: y.style }} />
          {y.thumbnails?.length > 0 && (
            <div className="yt-thumbs">
              {y.thumbnails.map((n) => <a key={n} href={`/api/stream/thumbs/${n}`} target="_blank" rel="noreferrer">
                <img src={`/api/stream/thumbs/${n}`} alt="" /></a>)}
            </div>
          )}
          {y.titles?.length > 0 && (<>
            <label className="field-label mt">Títulos sugeridos</label>
            {y.titles.map((t) => <div key={t} className="yt-line" onClick={() => copy(t)} title="Copiar">{t}</div>)}
          </>)}
          {y.thumbnail_text?.length > 0 && <p className="small muted">Texto para miniatura: {y.thumbnail_text.join(' · ')}</p>}
          <label className="field-label mt">Descripción con capítulos <button className="link-btn" onClick={() => copy(y.description)}>Copiar</button></label>
          <textarea className="input full" rows={7} readOnly value={y.description} />
          {y.tags?.length > 0 && (<>
            <label className="field-label mt">Etiquetas <button className="link-btn" onClick={() => copy(y.tags.join(', '))}>Copiar</button></label>
            <p className="small">{y.tags.join(', ')}</p>
          </>)}
        </div>
      ))}
    </div>
  )
}
