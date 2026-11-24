import { useCallback, useEffect, useState } from 'react'
import TastePanel from '../TastePanel.jsx'
import Icon from '../Icon.jsx'
import JobProgress from '../JobProgress.jsx'
import Recorder from './Recorder.jsx'
import { api, fmtDuration, uploadStudioFile } from '../../api.js'
import { useJob } from '../../hooks.js'

const post = (path, data) => fetch(`/api/studio${path}`, {
  method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data || {}),
}).then(async (r) => { const d = await r.json().catch(() => null); if (!r.ok) throw new Error(d?.detail || `Error ${r.status}`); return d })
const get = (path) => fetch(`/api/studio${path}`).then((r) => r.json())

// ---------------- Video desde un tema ----------------

export function TopicCreator({ status, notify, onCreated }) {
  const [topic, setTopic] = useState('')
  const [notes, setNotes] = useState('')
  const [duration, setDuration] = useState(45)
  const [format, setFormat] = useState('vertical')
  const [useVoice, setUseVoice] = useState(true)
  const [styleId, setStyleId] = useState('')
  const [styles, setStyles] = useState([])
  const [videos, setVideos] = useState([])
  const [selected, setSelected] = useState(new Set())
  const [jobId, setJobId] = useState(null)

  useEffect(() => {
    get('/styles').then(setStyles).catch(() => {})
    get('/footage').then((v) => setVideos(v.filter((x) => x.status === 'done'))).catch(() => {})
  }, [])

  const job = useJob(jobId, (j) => {
    if (j.status === 'done') { notify(`Video listo: ${j.result.lines} frases, voz ${j.result.voice}`); onCreated(j.result.project_id) }
    else if (j.status === 'error') notify(j.error, 'error')
  })

  const toggle = (id) => { const s = new Set(selected); s.has(id) ? s.delete(id) : s.add(id); setSelected(s) }
  const go = async () => {
    try {
      const j = await post('/topic', {
        topic, notes: notes || null, duration, format, use_voice: useVoice,
        video_ids: selected.size ? [...selected] : null, style_id: styleId ? Number(styleId) : null,
      })
      setJobId(j.id)
    } catch (e) { notify(e.message, 'error') }
  }
  const running = job && ['queued', 'running'].includes(job.status)
  const blockers = []
  if (!status?.llm_ready) blockers.push('Abre Ollama para escribir el guion.')
  if (!status?.tts_ready) blockers.push('El motor de voz no está instalado.')

  return (
    <div className="creator">
      <div className="panel">
        <h2>Video desde un tema</h2>
        <p className="muted small">La IA escribe el guion, lo narra con tu voz (con emociones) y lo ilustra con tomas de tus videos indexados. Después lo editas en el timeline.</p>
        {blockers.map((b) => <div key={b} className="alert alert-warn">{b}</div>)}
        <label className="field-label mt">Tema</label>
        <textarea className="input full" rows="2" value={topic} onChange={(e) => setTopic(e.target.value)}
          placeholder="Ej. 3 errores que todos cometen al empezar en Minecraft" />
        <label className="field-label mt">Indicaciones (opcional)</label>
        <textarea className="input full" rows="2" value={notes} onChange={(e) => setNotes(e.target.value)}
          placeholder="Tono, datos que quieres mencionar, a quién va dirigido, llamada a la acción…" />
        <div className="creator-row">
          <label>Duración
            <select className="input" value={duration} onChange={(e) => setDuration(+e.target.value)}>
              {[20, 30, 45, 60, 90].map((d) => <option key={d} value={d}>{d} s</option>)}
            </select>
          </label>
          <label>Formato
            <select className="input" value={format} onChange={(e) => setFormat(e.target.value)}>
              <option value="vertical">9:16 Vertical</option><option value="square">1:1</option><option value="horizontal">16:9</option>
            </select>
          </label>
          <label>Estilo
            <select className="input" value={styleId} onChange={(e) => setStyleId(e.target.value)}>
              <option value="">Mi estilo por defecto</option>
              {styles.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
            </select>
          </label>
        </div>
        <label className="check"><input type="checkbox" checked={useVoice} onChange={(e) => setUseVoice(e.target.checked)} />
          Usar mi voz clonada {status?.voice_profile ? '' : '(aún no la has creado: se usará una voz genérica)'}</label>
        <button className="btn btn-primary mt" disabled={topic.trim().length < 3 || running || blockers.length > 0} onClick={go}>
          <Icon name="spark" /> Crear video
        </button>
        {job && job.status !== 'done' && <JobProgress job={job} />}
        {zjob && zjob.status !== 'done' && zjob.status !== 'error' && <JobProgress job={zjob} />}
      </div>
      <div className="panel">
        <div className="panel-head"><h2>Clips a usar</h2><span className="muted small">{selected.size ? `${selected.size} elegidos` : 'Todos los indexados'}</span></div>
        {videos.length === 0 && <p className="muted small">No hay videos indexados. Ve a <b>Mis clips</b> e indexa tus videos para que la IA sepa qué contienen.</p>}
        <ul className="pick-list">
          {videos.map((v) => (
            <li key={v.id} className={selected.has(v.id) ? 'on' : ''} onClick={() => toggle(v.id)}>
              <input type="checkbox" readOnly checked={selected.has(v.id)} /> <span className="fname">{v.title}</span>
              <span className="muted small">{v.shots} tomas · {fmtDuration(v.duration)}</span>
            </li>
          ))}
        </ul>
      </div>
    </div>
  )
}

// ---------------- Mi voz ----------------

const SCRIPT_HINT = `Lee con naturalidad (ideal 1-3 minutos; mínimo 30 s). Si incluyes las secciones emocionales de tu guion
([Alegría] "¡No sabes lo feliz que estoy!…", [Sorpresa] "¡Espera un momento!…", [Seriedad] "Quiero que entiendas algo…",
[Entusiasmo] "¡Vamos a hacerlo!…", [Tranquilidad] "Respira profundamente…", [Confianza], [Reflexión]),
la app recorta cada una y tu voz clonada sonará como TÚ en esa emoción.`

export function VoicePanel({ status, notify, onChanged }) {
  const [busy, setBusy] = useState(null)
  const [jobId, setJobId] = useState(null)
  const [testJob, setTestJob] = useState(null)
  const [text, setText] = useState('¡No lo puedo creer! Esto que te voy a enseñar cambió mi forma de jugar.')
  const [emotion, setEmotion] = useState('sorpresa')
  const [sample, setSample] = useState(null)
  const prof = status?.voice_profile

  const job = useJob(jobId, (j) => {
    if (j.status === 'done') { notify(`Tu voz está lista (${j.result.duration}s, emociones: ${j.result.emotions.join(', ') || 'general'})`); onChanged() }
    else if (j.status === 'error') notify(j.error, 'error')
  })
  const tjob = useJob(testJob, (j) => {
    if (j.status === 'done') setSample(`${j.result.url}?t=${Date.now()}`)
    else if (j.status === 'error') notify(j.error, 'error')
  })

  const useFile = async (file, name) => {
    try {
      setBusy('Subiendo…')
      const up = await uploadStudioFile(file, (p) => setBusy(`Subiendo ${Math.round(p * 100)}%`), name)
      const j = await post('/voice', { upload_id: up.upload_id })
      setJobId(j.id); setBusy(null)
    } catch (e) { notify(e.message, 'error'); setBusy(null) }
  }
  const test = async () => {
    try { setSample(null); setTestJob((await post('/voice/test', { text, emotion })).id) } catch (e) { notify(e.message, 'error') }
  }

  return (
    <div className="creator">
      <div className="panel">
        <h2>Mi voz clonada</h2>
        <p className="muted small">Chatterbox Multilingual (local, licencia MIT) aprende tu voz de una grabación y la usa en tus videos. Controla la emoción frase por frase. Los audios llevan una marca de agua inaudible del modelo.</p>
        {!status?.tts_ready && <div className="alert alert-warn">El motor de voz no está instalado.</div>}
        {prof ? (
          <div className="voice-ok">
            <div><b>Perfil creado</b> · {prof.duration}s de muestra</div>
            <div className="muted small">Emociones con referencia propia: {Object.keys(prof.refs).length ? Object.keys(prof.refs).join(', ') : 'ninguna (se usa tu tono general + control de emoción)'}</div>
            <audio controls src="/api/studio/voice/file/ref_general.wav" />
          </div>
        ) : <p className="muted">Aún no has creado tu voz.</p>}
        <details className="mt"><summary>Cómo grabar tu muestra</summary><p className="small pre">{SCRIPT_HINT}</p></details>
        <div className="mt">
          {busy ? <div className="muted">{busy}</div> : (
            <>
              <Recorder onRecorded={(blob) => useFile(blob, 'mi_voz.webm')} />
              <label className="btn mt">
                <Icon name="upload" /> Subir audio de mi voz
                <input type="file" accept="audio/*,video/*" hidden onChange={(e) => e.target.files[0] && useFile(e.target.files[0])} />
              </label>
            </>
          )}
        </div>
        {job && job.status !== 'done' && <JobProgress job={job} />}
      </div>
      <div className="panel">
        <h2>Probar mi voz</h2>
        <textarea className="input full" rows="3" value={text} onChange={(e) => setText(e.target.value)} />
        <div className="row mt">
          <select className="input" value={emotion} onChange={(e) => setEmotion(e.target.value)}>
            {Object.entries(status?.emotions || {}).map(([k, l]) => <option key={k} value={k}>{l}</option>)}
          </select>
          <button className="btn btn-primary" onClick={test} disabled={!status?.tts_ready || (tjob && ['queued', 'running'].includes(tjob.status))}>Generar</button>
        </div>
        {tjob && tjob.status !== 'done' && <JobProgress job={tjob} compact />}
        {sample && <audio className="mt" controls autoPlay src={sample} />}
      </div>
    </div>
  )
}

// ---------------- Mis clips (índice) ----------------

export function FootagePanel({ status, notify, onChanged }) {
  const [videos, setVideos] = useState([])
  const [open, setOpen] = useState(null)
  const [shots, setShots] = useState([])
  const [jobId, setJobId] = useState(null)
  const load = useCallback(() => get('/footage').then(setVideos).catch(() => {}), [])
  useEffect(() => { load() }, [load])
  const job = useJob(jobId, (j) => {
    if (j.status === 'done') notify(`Indexado: ${j.result.videos} videos, ${j.result.shots} tomas`)
    else if (j.status === 'error') notify(j.error, 'error')
    load(); onChanged()
  })
  const importAll = async () => {
    try { const r = await post('/footage/import-all'); notify(`${r.imported} videos nuevos en la biblioteca (${r.found} encontrados)`); load() } catch (e) { notify(e.message, 'error') }
  }
  const index = async (ids = null, force = false) => {
    try { setJobId((await post('/footage/index', { video_ids: ids, force })).id) } catch (e) { notify(e.message, 'error') }
  }
  const show = async (id) => { if (open === id) { setOpen(null); return } setOpen(id); setShots(await get(`/footage/${id}/shots`)) }
  const running = job && ['queued', 'running'].includes(job.status)

  return (
    <div className="panel">
      <div className="panel-head">
        <h2>Mis clips</h2>
        <div className="row">
          <button className="btn" onClick={importAll}>Importar todos mis videos</button>
          <button className="btn btn-primary" disabled={running || !status?.vision_ready} onClick={() => index()}><Icon name="spark" /> Indexar con IA</button>
        </div>
      </div>
      <p className="muted small">La IA de visión ({status?.vision_model}) mira fotogramas de cada video y describe qué hay (juego, escena, acción). Así puede elegir tomas que ilustren tu guion. ~2 s por fotograma tras la primera carga.</p>
      {!status?.vision_ready && <div className="alert alert-warn">Necesitas Ollama abierto con {status?.vision_model}.</div>}
      {job && job.status !== 'done' && <JobProgress job={job} />}
      <ul className="filelist">
        {videos.map((v) => (
          <li key={v.id}>
            <div className="file-row foot-row" onClick={() => v.status === 'done' && show(v.id)}>
              <Icon name="film" /> <span className="fname">{v.title}</span>
              <span className="muted small">{fmtDuration(v.duration)}</span>
              <span>{v.status === 'done' ? <span className="badge badge-ok">{v.shots} tomas</span> : <span className="badge badge-warn">Sin indexar</span>}</span>
              <button className="btn sm" disabled={running} onClick={(e) => { e.stopPropagation(); index([v.id], true) }}>{v.status === 'done' ? 'Reindexar' : 'Indexar'}</button>
            </div>
            {open === v.id && (
              <div className="shot-grid">
                {shots.map((s) => (
                  <div key={s.id} className={`shot ${s.useful ? '' : 'useless'}`} title={s.tags}>
                    <img src={s.thumb_url} alt="" loading="lazy" />
                    <div className="small">{fmtDuration(s.start_s)} · acción {s.action}</div>
                    <div className="muted small">{s.caption}</div>
                  </div>
                ))}
              </div>
            )}
          </li>
        ))}
      </ul>
    </div>
  )
}

// ---------------- Estilos aprendidos ----------------

function Md({ text }) {
  if (!text) return null
  return (
    <div className="md">
      {text.split('\n').map((l, i) => {
        if (l.startsWith('## ')) return <h3 key={i}>{l.slice(3)}</h3>
        if (l.startsWith('# ')) return <h3 key={i}>{l.slice(2)}</h3>
        if (/^\s*[-*] /.test(l)) return <li key={i}>{l.replace(/^\s*[-*] /, '').replace(/\*\*/g, '')}</li>
        if (/^\s*\d+\. /.test(l)) return <li key={i}>{l.replace(/^\s*\d+\. /, '').replace(/\*\*/g, '')}</li>
        return l.trim() ? <p key={i}>{l.replace(/\*\*/g, '')}</p> : null
      })}
    </div>
  )
}

function RefCookies({ notify }) {
  const [c, setC] = useState(null)
  const [open, setOpen] = useState(false)
  useEffect(() => { get('/ref-cookies').then(setC).catch(() => {}) }, [])
  if (!c) return null
  const on = c.file || c.browser
  const setBrowser = async (browser) => {
    try { setC(await fetch('/api/studio/ref-cookies', { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ browser }) }).then((r) => r.json())); notify(browser ? `Usaré la sesión de ${browser}` : 'Sin navegador') } catch (e) { notify(e.message, 'error') }
  }
  const upload = async (file) => {
    const fd = new FormData(); fd.append('file', file)
    const r = await fetch('/api/studio/ref-cookies/file', { method: 'POST', body: fd })
    const d = await r.json()
    if (r.ok) { setC(d); notify('cookies.txt guardado') } else notify(d.detail || 'Archivo no válido', 'error')
  }
  return (
    <div className="ref-cookies small">
      <button className="link-btn small" onClick={() => setOpen(!open)}>
        🔞 Videos con restricción de edad o privados {on ? '· sesión configurada ✓' : '· sin configurar'}
      </button>
      {open && (
        <div className="mt">
          <p className="muted">YouTube pide iniciar sesión para algunos videos. La app primero intenta sin sesión y, solo si hace falta, usa la tuya:</p>
          <label className="field-label">Opción A: navegador donde tienes YouTube abierto con tu cuenta</label>
          <select className="input" value={c.browser} onChange={(e) => setBrowser(e.target.value)}>
            <option value="">No usar navegador</option>
            {c.browsers.map((b) => <option key={b} value={b}>{b[0].toUpperCase() + b.slice(1)}</option>)}
          </select>
          <div className="muted">Firefox es el que mejor funciona. Chrome y Edge suelen bloquear sus cookies: con ellos usa la opción B.</div>
          <label className="field-label mt">Opción B: archivo cookies.txt</label>
          <div className="muted">Instala la extensión «Get cookies.txt LOCALLY», abre youtube.com con tu sesión, exporta y súbelo aquí. Se guarda solo en tu PC y tiene prioridad sobre el navegador.</div>
          <div className="row mt">
            <label className="btn sm"><Icon name="upload" size={14} /> {c.file ? 'Reemplazar cookies.txt' : 'Subir cookies.txt'}
              <input type="file" accept=".txt" hidden onChange={(e) => e.target.files[0] && upload(e.target.files[0])} />
            </label>
            {c.file && <button className="btn sm" onClick={async () => { setC(await fetch('/api/studio/ref-cookies/file', { method: 'DELETE' }).then((r) => r.json())); notify('cookies.txt eliminado') }}>Quitar archivo</button>}
          </div>
        </div>
      )}
    </div>
  )
}

export function StylesPanel({ notify }) {
  const [styles, setStyles] = useState([])
  const [videos, setVideos] = useState([])
  const [picked, setPicked] = useState([])
  const [urls, setUrls] = useState('')
  const [uploads, setUploads] = useState([])
  const [target, setTarget] = useState('')
  const [name, setName] = useState('')
  const [jobId, setJobId] = useState(null)
  const [open, setOpen] = useState(null)
  const [busy, setBusy] = useState(null)
  const [libOpen, setLibOpen] = useState(false)
  const [ready, setReady] = useState({})
  const load = useCallback(() => get('/styles').then(setStyles).catch(() => {}), [])
  useEffect(() => {
    load()
    api.library().then((d) => setVideos(d.items)).catch(() => {})
    get('/status').then((s) => setReady({ earshot: s.earshot_ready, ytdlp: s.ytdlp_ready })).catch(() => {})
  }, [load])
  const job = useJob(jobId, (j) => {
    if (j.status === 'done') {
      const f = j.result.failed || []
      notify(`Estilo aprendido de ${j.result.videos} video(s)${f.length ? ` · ${f.length} no se pudieron usar` : ''}`)
      if (f.length) notify(f.map((x) => x.error).join(' · '), 'error')
      setOpen(j.result.style_id); setUrls(''); setPicked([]); setUploads([])
    } else if (j.status === 'error') notify(j.error, 'error')
    load()
  })
  const linkList = urls.split(/[\s,]+/).map((u) => u.trim()).filter((u) => /^https?:\/\//i.test(u))
  const total = linkList.length + picked.length + uploads.length
  const learn = async () => {
    try {
      const body = { name: name || null, urls: linkList, video_ids: picked, style_id: target ? Number(target) : null,
        sources: uploads.map((u) => ({ upload_id: u.upload_id, source_name: u.name })) }
      setJobId((await post('/learn', body)).id)
    } catch (e) { notify(e.message, 'error') }
  }
  const upload = async (files) => {
    for (const file of files) {
      try {
        setBusy(`Subiendo ${file.name}…`)
        const up = await uploadStudioFile(file, (p) => setBusy(`Subiendo ${file.name} ${Math.round(p * 100)}%`))
        setUploads((x) => [...x, { upload_id: up.upload_id, name: file.name }])
      } catch (e) { notify(e.message, 'error') }
    }
    setBusy(null)
  }
  const del = async (s) => {
    if (!window.confirm(`¿Eliminar el estilo "${s.name}"? No se puede deshacer.`)) return
    const r = await fetch(`/api/studio/styles/${s.id}`, { method: 'DELETE' })
    if (r.ok) { notify(`Estilo "${s.name}" eliminado`); if (target === String(s.id)) setTarget(''); if (open === s.id) setOpen(null) } else notify('No se pudo eliminar el estilo', 'error')
    load()
  }
  const [zoomJob, setZoomJob] = useState(null)
  const zjob = useJob(zoomJob, (j) => {
    if (j.status === 'done') {
      const r = j.result
      notify(r.kind === 'none' ? 'Medido: tu referencia no usa zooms; las ediciones tampoco los pondrán'
        : `Zooms medidos: ${r.zoom_per_min}/min · ${Math.round(((r.amount || 1) - 1) * 100)} % · ${r.hold_s} s${r.missing?.length ? ` · faltan ${r.missing.length} video(s)` : ''}`)
    } else if (j.status === 'error') notify(j.error, 'error')
    load()
  })
  const measureZooms = async (s) => {
    try { setZoomJob((await post(`/styles/${s.id}/zooms`, {})).id) } catch (e) { notify(e.message, 'error') }
  }
  const zoomRunning = zjob && ['queued', 'running'].includes(zjob.status)
  const running = job && ['queued', 'running'].includes(job.status)
  const toggle = (id) => setPicked((x) => (x.includes(id) ? x.filter((y) => y !== id) : [...x, id]))

  return (
    <div className="creator">
      <div className="panel">
        <h2>Aprender un estilo de edición</h2>
        <p className="muted small">Dale varios videos cuyo estilo te guste y la app los "desarma": mide cortes, destellos, ritmo del habla y pausas;
          la IA de visión estudia subtítulos, tipografía, colores y composición; y <b>earshot</b> escucha el diseño de sonido (qué efectos usan, cuántos por minuto, si caen en los cortes, cuánta música).
          Con varios videos aprende <b>lo que se repite</b> entre ellos, no lo de uno suelto.</p>
        <div className="learn-ready small">
          <span className={ready.ytdlp ? 'ok' : 'off'}>{ready.ytdlp ? '✓' : '✗'} Enlaces (yt-dlp)</span>
          <span className={ready.earshot ? 'ok' : 'off'}>{ready.earshot ? '✓' : '✗'} Escucha de sonido (earshot)</span>
        </div>

        <label className="field-label mt">Enlaces (uno por línea): YouTube, Shorts, TikTok, Instagram, X, Twitch…</label>
        <textarea className="input full" rows={4} value={urls} onChange={(e) => setUrls(e.target.value)}
          placeholder={'https://www.youtube.com/shorts/…\nhttps://www.tiktok.com/@creador/video/…'} />
        <RefCookies notify={notify} />

        <div className="learn-row mt">
          <button className="btn" onClick={() => setLibOpen(!libOpen)}>📚 De mi biblioteca{picked.length ? ` (${picked.length})` : ''}</button>
          {busy ? <span className="muted small">{busy}</span> : (
            <label className="btn"><Icon name="upload" /> Subir archivos
              <input type="file" accept="video/*" multiple hidden onChange={(e) => e.target.files.length && upload([...e.target.files])} />
            </label>
          )}
        </div>
        {libOpen && (
          <div className="learn-lib">
            {videos.map((v) => (
              <label key={v.id} className="check"><input type="checkbox" checked={picked.includes(v.id)} onChange={() => toggle(v.id)} /> {v.title}</label>
            ))}
          </div>
        )}
        {uploads.length > 0 && <div className="muted small mt">Archivos: {uploads.map((u) => u.name).join(', ')}</div>}

        <div className="learn-row mt">
          <select className="input" value={target} onChange={(e) => setTarget(e.target.value)}>
            <option value="">Crear un estilo nuevo</option>
            {styles.map((s) => <option key={s.id} value={s.id}>Sumar a «{s.name}» ({s.profile.n_videos || 1} videos)</option>)}
          </select>
          {!target && <input className="input" value={name} onChange={(e) => setName(e.target.value)} placeholder="Nombre del estilo (opcional)" />}
        </div>
        <button className="btn btn-primary full mt" disabled={!total || running || !!busy} onClick={learn}>
          {running ? 'Aprendiendo…' : total ? `Aprender de ${total} video${total > 1 ? 's' : ''}` : 'Agrega enlaces o videos'}
        </button>
        <p className="muted small mt">Los enlaces se descargan solo para estudiarlos (máx. 30 min y 12 videos por vez). Úsalo con videos que tengas permiso de descargar; si una plataforma pide iniciar sesión, descárgalo tú y súbelo.</p>
        {job && job.status !== 'done' && <JobProgress job={job} />}
      </div>
      <div className="panel">
        <h2>Mis estilos ({styles.length})</h2>
        {styles.length === 0 && <p className="muted small">Aún no hay estilos aprendidos.</p>}
        {styles.map((s) => {
          const sp = s.profile.sound_pro
          return (
            <div key={s.id} className="style-card">
              <div className="style-head" onClick={() => setOpen(open === s.id ? null : s.id)}>
                <div>
                  <b>{s.name}</b>
                  <div className="muted small">{s.profile.n_videos > 1 ? `${s.profile.n_videos} videos · ` : ''}{s.profile.edit?.cuts_per_10s ?? '—'} cortes/10 s · {s.profile.delivery?.words_per_min ?? '—'} palabras/min · música: {s.profile.sound?.music_bed ?? '—'}</div>
                  {s.profile.zoom && <div className="muted small">🔍 {s.style.zoom_kind === 'none' ? 'sin zooms' : `${s.profile.zoom.zoom_per_min} zooms/min · ${Math.round(((s.profile.zoom.amount || 1) - 1) * 100)} % · se sostienen ${s.profile.zoom.hold_s ?? '—'} s · ${s.profile.zoom.kind === 'ramp' ? 'progresivos' : 'de golpe'}`}</div>}
                  {sp && <div className="muted small">🔊 {sp.sfx_per_min} efectos/min{sp.roles?.length ? ` · ${sp.roles.join(', ')}` : ''}{sp.cut_sync != null ? ` · ${Math.round(sp.cut_sync * 100)} % de cortes con sonido` : ''}{sp.music_ratio != null ? ` · música ${Math.round(sp.music_ratio * 100)} %` : ''}</div>}
                </div>
                <button className="icon-btn sm style-del" title="Eliminar estilo" onClick={(e) => { e.stopPropagation(); del(s) }}><Icon name="trash" size={14} /></button>
                <div className="swatches">
                  {[s.style.caption_color, s.style.highlight_color, s.style.title_bg, ...(s.profile.palette || []).slice(0, 3)].map((c, i) => <span key={i} style={{ background: c }} />)}
                </div>
              </div>
              {open === s.id && (
                <div className="style-body">
                  <div className="muted small">Subtítulos: {s.style.caption_style} · {s.style.caption_position} · {s.style.caption_max_words} palabras · {s.style.caption_font} · transiciones: {s.style.transition_style}</div>
                  {s.profile.videos?.length > 0 && (
                    <ul className="learn-videos small">
                      {s.profile.videos.map((v, i) => (
                        <li key={i}>{v.source.platform ? `[${v.source.platform}] ` : ''}{v.source.url ? <a href={v.source.url} target="_blank" rel="noreferrer">{v.source.title}</a> : v.source.title}
                          <span className="muted"> · {v.profile.edit?.cuts_per_10s ?? '—'} cortes/10 s{v.profile.sound_pro?.sfx_per_min != null ? ` · ${v.profile.sound_pro.sfx_per_min} efectos/min` : ''}</span>
                          {v.profile.zoom?.events?.length > 0 && <ZoomTrack z={v.profile.zoom} onToggle={async (index, enabled) => {
                            try { await post(`/styles/${s.id}/zooms/toggle`, { video: i, index, enabled }); load() } catch (e) { notify(e.message, 'error') }
                          }} />}</li>
                      ))}
                    </ul>
                  )}
                  <Md text={s.report} />
                  <div className="row mt">
                    <button className="btn" onClick={async () => { await post(`/styles/${s.id}/default`); notify('Será tu estilo para proyectos nuevos') }}>Usar por defecto</button>
                    <button className="btn" onClick={() => { setTarget(String(s.id)); window.scrollTo({ top: 0, behavior: 'smooth' }) }}>+ Sumar más videos</button>
                    <button className="btn" disabled={zoomRunning} title="Detecta los zooms reales de cámara de estos videos (cuántos, cuánto acercan y cuánto duran)"
                      onClick={() => measureZooms(s)}>{zoomRunning ? 'Midiendo zooms…' : s.profile.zoom ? '🔍 Volver a medir zooms' : '🔍 Medir zooms'}</button>
                    <button className="btn" onClick={() => del(s)}><Icon name="trash" size={14} /> Eliminar estilo</button>
                  </div>
                </div>
              )}
            </div>
          )
        })}
      </div>
      <TastePanel notify={notify} />
      <KnowledgePanel notify={notify} />
    </div>
  )
}

// Zooms detectados en un video de referencia, como marcas sobre su duración. Clic = quitar/poner (falsos positivos).
function ZoomTrack({ z, onToggle }) {
  const len = Math.max(1, z.analyzed_s || Math.max(...z.events.map((e) => e.t1 || e.t0)) + 2)
  const tc = (t) => `${Math.floor(t / 60)}:${String(Math.floor(t % 60)).padStart(2, '0')}`
  return (
    <div className="zoom-track" title="Zooms detectados. Clic en una marca para quitarla si no es un zoom de edición.">
      {z.events.map((e, k) => (
        <button key={k} type="button"
          className={`zoom-mark ${e.dir === 'in' ? 'in' : 'out'} ${e.off ? 'off' : ''} ${e.kind === 'ramp' ? 'ramp' : ''}`}
          style={{ left: `${(e.t0 / len) * 100}%`, width: e.kind === 'ramp' ? `${Math.max(0.6, ((e.t1 - e.t0) / len) * 100)}%` : undefined }}
          title={`${tc(e.t0)} · ${e.dir === 'in' ? 'acerca' : 'aleja'} ${Math.round(Math.abs(e.amount - 1) * 100)} % · ${e.kind === 'ramp' ? 'progresivo' : 'de golpe'}${e.hold_s ? ` · ${e.hold_s} s` : ''}${e.off ? ' · QUITADO' : ''}`}
          onClick={() => onToggle(k, !!e.off)} />
      ))}
    </div>
  )
}

function KnowledgePanel({ notify }) {
  const [g, setG] = useState(null)
  const [text, setText] = useState('')
  const [fb, setFb] = useState([])
  const load = useCallback(() => {
    fetch('/api/knowledge/guide').then((r) => r.json()).then((d) => { setG(d); setText(d.text) }).catch(() => {})
    fetch('/api/feedback?limit=20').then((r) => r.json()).then(setFb).catch(() => {})
  }, [])
  useEffect(() => { load() }, [load])
  const save = async (t) => {
    const r = await fetch('/api/knowledge/guide', { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ text: t }) })
    if (r.ok) { notify(t ? 'Guía guardada: la IA la usará en cada edición' : 'Guía restablecida'); load() } else notify('No se pudo guardar', 'error')
  }
  const delFb = async (id) => { await fetch(`/api/feedback/${id}`, { method: 'DELETE' }); load() }
  if (!g) return null
  return (
    <div className="panel knowledge-panel">
      <h2>Lo que sabe tu IA de edición</h2>
      <p className="muted small">Antes de cada edición (clips, video de YouTube, auto-edición) la IA lee esta guía, el análisis del estilo
        aprendido que elijas y tus opiniones (👍/👎, clips aprobados o descartados). Escríbele tus reglas: «memes en cada fail»,
        «nunca subtítulos amarillos», «gancho con mi cara», etc.</p>
      <textarea className="input full mono" rows={14} value={text} onChange={(e) => setText(e.target.value)} />
      <div className="row mt">
        <button className="btn btn-primary" disabled={text === g.text} onClick={() => save(text)}>Guardar guía</button>
        {g.custom && <button className="btn" onClick={() => { if (window.confirm('¿Volver a la guía original?')) save('') }}>Restablecer</button>}
      </div>
      <h3 className="mt">Lo que ha aprendido de ti ({fb.length})</h3>
      {fb.length === 0 && <p className="muted small">Aún no hay opiniones. Usa 👍/👎 en tus videos de YouTube y aprueba o descarta clips.</p>}
      <ul className="fb-list">
        {fb.map((f) => (
          <li key={f.id}>
            <span>{f.rating > 0 ? '👍' : f.rating < 0 ? '👎' : '📝'}</span>
            <span className="small">{f.context?.title ? <b>{f.context.title}: </b> : null}{f.note || (f.rating > 0 ? 'le gustó' : 'no le gustó')}</span>
            <span className="muted small">{f.created_at}</span>
            <button className="icon-btn sm" title="Olvidar" onClick={() => delFb(f.id)}><Icon name="x" /></button>
          </li>
        ))}
      </ul>
    </div>
  )
}
