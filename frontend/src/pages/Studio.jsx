import { useCallback, useEffect, useState } from 'react'
import BriefForm, { EMPTY_BRIEF } from '../components/studio/BriefForm.jsx'
import Icon from '../components/Icon.jsx'
import Recorder from '../components/studio/Recorder.jsx'
import { FootagePanel, StylesPanel, TopicCreator, VoicePanel } from '../components/studio/Creators.jsx'
import StudioEditor from './StudioEditor.jsx'
import { api, fmtDuration, getQuery, uploadStudioFile } from '../api.js'

export default function Studio({ navigate, notify, hash }) {
  const pid = Number(getQuery().p) || null
  if (pid) return <StudioEditor key={pid} pid={pid} navigate={navigate} notify={notify} />
  return <ProjectList navigate={navigate} notify={notify} hash={hash} />
}

function ProjectList({ notify }) {
  const [items, setItems] = useState(null)
  const [creating, setCreating] = useState(false)
  const [status, setStatus] = useState(null)
  const [tab, setTab] = useState('projects')

  const load = useCallback(() => {
    api.studioProjects().then(setItems).catch((e) => notify(e.message, 'error'))
  }, [notify])
  const loadStatus = useCallback(() => api.studioStatus().then(setStatus).catch(() => {}), [])
  useEffect(() => { load(); loadStatus() }, [load, loadStatus])

  const open = (id) => { window.location.hash = `#/studio?p=${id}` }
  const remove = async (p) => {
    if (!window.confirm(`¿Eliminar el proyecto "${p.name}" y sus renders?`)) return
    try { await api.studioDelete(p.id); load() } catch (e) { notify(e.message, 'error') }
  }

  return (
    <div className="page">
      <header className="page-head">
        <div>
          <h1>Studio</h1>
          <p className="muted">Crea contenido original: graba o sube tu video o tu voz y la IA arma la edición. Después ajustas todo en el timeline.</p>
        </div>
        <button className="btn btn-primary" onClick={() => setCreating(true)}><Icon name="plus" /> Nuevo proyecto</button>
      </header>

      {status && (
        <div className="ai-bar">
          <span className={`ai-chip ${status.llm_ready ? 'ok' : 'warn'}`}><span className="dot" /> IA editora {status.llm_model} <span className="muted">· {status.llm_ready ? 'lista' : status.llm_backend === 'llamacpp' ? 'llama.cpp no está listo' : 'Ollama no está corriendo'}</span></span>
          <span className={`ai-chip ${status.tts_ready ? 'ok' : 'warn'}`}><span className="dot" /> Voz <span className="muted">· {status.voice_profile ? 'clonada lista' : status.tts_ready ? 'motor listo, falta tu muestra' : 'no instalada'}</span></span>
          <span className={`ai-chip ${status.vision_ready ? 'ok' : 'warn'}`}><span className="dot" /> Visión <span className="muted">· {status.footage.indexed}/{status.footage.videos} videos indexados</span></span>
          <span className="ai-chip ok"><span className="dot" /> Recursos <span className="muted">· {status.assets.music} música · {status.assets.sfx} efectos · {status.assets.broll} b-roll</span></span>
        </div>
      )}

      <div className="seg studio-tabs">
        {[['projects', 'Proyectos'], ['topic', 'Desde un tema'], ['voice', 'Mi voz'], ['footage', 'Mis clips'], ['styles', 'Estilos aprendidos']].map(([k, l]) => (
          <button key={k} className={tab === k ? 'on' : ''} onClick={() => setTab(k)}>{l}</button>
        ))}
      </div>
      {tab === 'topic' && <TopicCreator status={status} notify={notify} onCreated={open} />}
      {tab === 'voice' && <VoicePanel status={status} notify={notify} onChanged={loadStatus} />}
      {tab === 'footage' && <FootagePanel status={status} notify={notify} onChanged={loadStatus} />}
      {tab === 'styles' && <StylesPanel notify={notify} />}
      {tab === 'projects' && <>

      {items?.length === 0 ? (
        <div className="panel empty">
          <Icon name="film" size={32} />
          <p>Aún no tienes proyectos. Graba tu primera toma o sube un video/voz en off.</p>
          <button className="btn btn-primary" onClick={() => setCreating(true)}>Crear mi primer proyecto</button>
        </div>
      ) : (
        <div className="grid">
          {(items || []).map((p) => (
            <div key={p.id} className="proj-card" onClick={() => open(p.id)}>
              <div className="proj-icon"><Icon name={p.source_kind === 'audio' ? 'spark' : 'film'} size={26} /></div>
              <div className="proj-body">
                <div className="vcard-title">{p.name}</div>
                <div className="muted small">{p.source_kind === 'audio' ? 'Voz en off' : 'Video'} · {p.format} · {fmtDuration(p.duration)}</div>
                <div className="muted small">Editado {p.updated_at}</div>
              </div>
              <button className="icon-btn sm" title="Eliminar" onClick={(e) => { e.stopPropagation(); remove(p) }}><Icon name="trash" size={14} /></button>
            </div>
          ))}
        </div>
      )}
      </>}
      {creating && <NewProject onClose={() => setCreating(false)} notify={notify} onCreated={open} />}
    </div>
  )
}

function NewProject({ onClose, notify, onCreated }) {
  const [brief, setBrief] = useState(EMPTY_BRIEF)
  const [showBrief, setShowBrief] = useState(true)
  const [tab, setTab] = useState('record')
  const [name, setName] = useState('')
  const [format, setFormat] = useState('vertical')
  const [useAi, setUseAi] = useState(true)
  const [busy, setBusy] = useState(null)
  const [videos, setVideos] = useState([])
  const [videoId, setVideoId] = useState('')

  useEffect(() => { api.library().then((d) => setVideos(d.items)).catch(() => {}) }, [])

  const create = async (source) => {
    try {
      setBusy('Creando proyecto…')
      const r = await api.studioCreate({ name: name.trim() || 'Proyecto sin título', format, use_ai: useAi, brief, ...source })
      notify('Proyecto creado: la IA está editando tu toma')
      onCreated(r.project.id)
    } catch (e) { notify(e.message, 'error'); setBusy(null) }
  }

  const uploadAndCreate = async (file, filename) => {
    try {
      setBusy('Subiendo 0%')
      const up = await uploadStudioFile(file, (p) => setBusy(`Subiendo ${Math.round(p * 100)}%`), filename)
      await create({ source_type: 'upload', upload_id: up.upload_id })
    } catch (e) { notify(e.message, 'error'); setBusy(null) }
  }

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal modal-form" onClick={(e) => e.stopPropagation()}>
        <button className="icon-btn modal-close" onClick={onClose}><Icon name="x" /></button>
        <h2>Nuevo proyecto</h2>
        <div className="form-grid">
          <label className="field-label">Nombre</label>
          <input className="input full" value={name} onChange={(e) => setName(e.target.value)} placeholder="Ej. 3 errores al invertir" />
          <label className="field-label mt">Formato</label>
          <div className="seg">
            {[['vertical', '9:16 Vertical'], ['square', '1:1 Cuadrado'], ['horizontal', '16:9 Horizontal']].map(([v, l]) => (
              <button key={v} className={format === v ? 'on' : ''} onClick={() => setFormat(v)}>{l}</button>
            ))}
          </div>
          <label className="check"><input type="checkbox" checked={useAi} onChange={(e) => setUseAi(e.target.checked)} /> Que la IA añada título, textos, b-roll, música y efectos</label>
        </div>
        <button className="link-btn mt" onClick={() => setShowBrief(!showBrief)}>{showBrief ? '▾' : '▸'} Cómo quieres que se edite (tipo de video, subtítulos, videos de apoyo, instrucciones)</button>
        {showBrief && <BriefForm value={brief} onChange={setBrief} format={format} excludeVideoId={videoId ? Number(videoId) : null} />}

        <div className="seg seg-full mt">
          {[['record', 'Grabar ahora'], ['upload', 'Subir archivo'], ['library', 'De la biblioteca']].map(([k, l]) => (
            <button key={k} className={tab === k ? 'on' : ''} onClick={() => setTab(k)}>{l}</button>
          ))}
        </div>

        <div className="new-src">
          {busy ? <div className="muted">{busy}</div> : (
            <>
              {tab === 'record' && <Recorder onRecorded={(blob, mode) => uploadAndCreate(blob, mode === 'video' ? 'grabacion.webm' : 'voz.webm')} />}
              {tab === 'upload' && (
                <label className="dropzone">
                  <Icon name="upload" size={28} />
                  <span>Elige tu video o tu pista de voz en off</span>
                  <span className="muted small">MP4, MOV, MKV, WEBM, MP3, WAV, M4A…</span>
                  <input type="file" accept="video/*,audio/*" hidden onChange={(e) => e.target.files[0] && uploadAndCreate(e.target.files[0])} />
                </label>
              )}
              {tab === 'library' && (
                <div className="row">
                  <select className="input" value={videoId} onChange={(e) => setVideoId(e.target.value)}>
                    <option value="">Elige un video de tu biblioteca…</option>
                    {videos.map((v) => <option key={v.id} value={v.id}>{v.title}</option>)}
                  </select>
                  <button className="btn btn-primary" disabled={!videoId} onClick={() => create({ source_type: 'library', video_id: Number(videoId) })}>Crear</button>
                </div>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  )
}
