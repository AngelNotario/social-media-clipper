import { useCallback, useEffect, useState } from 'react'
import Icon from '../components/Icon.jsx'
import RolesPanel from '../components/RolesPanel.jsx'
import ModelPicker, { useModelChoice } from '../components/ModelPicker.jsx'
import JobProgress from '../components/JobProgress.jsx'
import ClipReview from '../components/ClipReview.jsx'
import { api, fmtDuration, fmtTime, getQuery } from '../api.js'
import { useJob } from '../hooks.js'

const STATUS_LABEL = { pending: 'Pendiente', approved: 'Aprobado', discarded: 'Descartado' }
const SOURCE_LABEL = { ai: 'IA', heuristic: 'Heurístico', manual: 'Manual', energy: 'Emoción' }

export default function Clips({ navigate, notify, hash }) {
  const [videos, setVideos] = useState(null)
  const [ai, setAi] = useState(null)
  const videoId = Number(getQuery().video) || null

  const [finals, setFinals] = useState([]) // videos exportados del Studio que aún no se usan aquí
  const [listTab, setListTab] = useState('orig')
  const [sending, setSending] = useState(null)

  const loadVideos = useCallback(() => {
    api.videosOverview().then(setVideos).catch((e) => notify(e.message, 'error'))
    fetch('/api/studio/finals').then((r) => r.json()).then((f) => setFinals((f || []).filter((x) => x.available && !x.library_video_id))).catch(() => {})
  }, [notify])

  const sendFinal = async (f) => {
    setSending(f.id)
    try {
      const r = await fetch(`/api/studio/renders/${f.id}/to-library`, { method: 'POST' })
      const j = await r.json()
      if (!r.ok) throw new Error(j.detail || 'No se pudo preparar el video')
      loadVideos()
      window.location.hash = `#/clips?video=${j.video_id}`
    } catch (e) { notify(e.message, 'error') }
    setSending(null)
  }
  const originals = (videos || []).filter((v) => !v.from_studio)
  const edited = (videos || []).filter((v) => v.from_studio)
  const cur = (videos || []).find((v) => v.id === videoId)
  const tabNow = cur ? (cur.from_studio ? 'studio' : 'orig') : listTab

  useEffect(() => { loadVideos() }, [loadVideos, hash])
  useEffect(() => { api.aiStatus().then(setAi).catch(() => {}) }, [])

  const select = (id) => { window.location.hash = `#/clips?video=${id}` }

  return (
    <div className="page page-wide">
      <header className="page-head">
        <div>
          <h1>Clips con IA</h1>
          <p className="muted">Analiza un video, revisa los momentos propuestos y genera clips verticales.</p>
        </div>
      </header>

      <AiStatusBar ai={ai} />

      <div className="clips-layout">
        <aside className="video-list panel">
          <div className="panel-head"><h2>Videos</h2></div>
          <div className="seg mb" style={{ width: '100%' }}>
            <button className={tabNow === 'orig' ? 'on' : ''} onClick={() => setListTab('orig')} title="Tus grabaciones originales">Originales ({originals.length})</button>
            <button className={tabNow === 'studio' ? 'on' : ''} onClick={() => setListTab('studio')} title="Videos ya editados y exportados en el Studio">Editados en Studio ({edited.length + finals.length})</button>
          </div>
          {tabNow === 'orig' && videos?.length === 0 && (
            <div className="empty small">
              <p>Importa videos primero.</p>
              <button className="btn" onClick={() => navigate('explorer')}>Explorar archivos</button>
            </div>
          )}
          {tabNow === 'studio' && edited.length + finals.length === 0 && (
            <div className="empty small"><p>Cuando exportes un video en el Studio aparecerá aquí para sacarle clips.</p></div>
          )}
          {tabNow === 'studio' && finals.map((f) => (
            <button key={`f${f.id}`} className="vl-item" disabled={sending === f.id} onClick={() => sendFinal(f)}
              title="Preparar este video editado para sacarle clips">
              <div className="vl-nothumb"><Icon name="film" /></div>
              <div className="vl-body">
                <div className="vl-title">{f.title}</div>
                <div className="vl-sub">{fmtDuration(f.duration)} · {sending === f.id ? 'Preparando…' : <span className="txt-accent">Usar para clips</span>}</div>
              </div>
            </button>
          ))}
          {(tabNow === 'studio' ? edited : originals).map((v) => (
            <button key={v.id} className={`vl-item ${v.id === videoId ? 'on' : ''}`} onClick={() => select(v.id)}>
              {v.thumbnail_url ? <img src={v.thumbnail_url} alt="" /> : <div className="vl-nothumb"><Icon name="film" /></div>}
              <div className="vl-body">
                <div className="vl-title">{v.title}</div>
                <div className="vl-sub">
                  {fmtDuration(v.duration)} · <VideoState v={v} />
                </div>
              </div>
            </button>
          ))}
        </aside>

        <section className="clips-main">
          {videoId
            ? <VideoWorkspace key={videoId} videoId={videoId} ai={ai} notify={notify} onChanged={loadVideos} />
            : <div className="panel empty"><Icon name="spark" size={32} /><p>Elige un video de la lista para analizarlo o revisar sus clips.</p></div>}
        </section>
      </div>
    </div>
  )
}

function VideoState({ v }) {
  if (v.job_status === 'running' || v.job_status === 'queued') return <span className="txt-accent">Analizando…</span>
  if (v.job_status === 'error' && !v.clips) return <span className="bad">Error</span>
  if (!v.has_audio) return <span className="muted">Sin audio</span>
  if (v.clips) return <span>{v.clips} clips{v.approved ? ` · ${v.approved} ✓` : ''}</span>
  return <span className="muted">Sin analizar</span>
}

function AiStatusBar({ ai }) {
  if (!ai) return null
  const items = [
    { ok: ai.whisper.installed, label: `Whisper ${ai.whisper.model}`, extra: ai.whisper.installed ? (ai.whisper.cuda ? 'GPU' : 'CPU') : 'no instalado' },
    { ok: ai.ollama.available && ai.ollama.model_installed, label: ai.backend === 'llamacpp' ? 'llama.cpp · Qwen3.6 35B' : `Ollama ${ai.ollama.model}`,
      extra: !ai.ollama.available ? 'no está corriendo' : ai.ollama.model_installed ? 'listo' : 'modelo no descargado' },
    { ok: true, label: 'Render', extra: ai.nvenc ? 'GPU NVENC' : 'CPU (x264)' },
  ]
  return (
    <div className="ai-bar">
      {items.map((i) => (
        <span key={i.label} className={`ai-chip ${i.ok ? 'ok' : 'warn'}`}>
          <span className="dot" /> {i.label} <span className="muted">· {i.extra}</span>
        </span>
      ))}
      {!(ai.ollama.available && ai.ollama.model_installed) && (
        <span className="muted small">Sin Ollama se usa selección heurística (sin IA).</span>
      )}
    </div>
  )
}

function VideoWorkspace({ videoId, ai, notify, onChanged }) {
  const [video, setVideo] = useState(null)
  const [state, setState] = useState(null)
  const [clips, setClips] = useState(null)
  const [selected, setSelected] = useState(null)
  const [filter, setFilter] = useState('all')
  const [jobId, setJobId] = useState(null)
  const [opts, setOpts] = useState({ max_clips: 10, min_dur: 20, max_dur: 60, judge: true })

  const loadClips = useCallback(async () => {
    const list = await api.clips(videoId)
    setClips(list)
    setSelected((cur) => (cur && list.find((c) => c.id === cur) ? cur : list[0]?.id ?? null))
  }, [videoId])

  const loadState = useCallback(async () => {
    const s = await api.analysis(videoId)
    setState(s)
    if (s.job && ['queued', 'running'].includes(s.job.status)) setJobId(s.job.id)
  }, [videoId])

  useEffect(() => {
    api.video(videoId).then(setVideo).catch((e) => notify(e.message, 'error'))
    loadState().catch(() => {})
    loadClips().catch(() => {})
  }, [videoId, loadState, loadClips, notify])

  const job = useJob(jobId, (j) => {
    if (j.status === 'done') {
      notify(`Análisis listo: ${j.result.clips} clips (${j.result.model ? `IA · ${j.result.model}` : 'heurístico'})`)
    } else if (j.status === 'error') notify(j.error, 'error')
    loadClips(); loadState(); onChanged()
  })

  const [model, setModel] = useModelChoice()
  const start = async (retranscribe = false) => {
    try {
      const j = await api.analyze(videoId, { ...opts, retranscribe, mode: state?.analysis?.params?.mode || 'speech', llm_model: model || null })
      setJobId(j.id)
      onChanged()
    } catch (e) { notify(e.message, 'error') }
  }

  const addManual = async () => {
    try {
      const c = await api.createClip({ video_id: videoId, start_s: 0, end_s: Math.min(30, video.duration || 30), title: 'Clip manual' })
      await loadClips()
      setSelected(c.id)
    } catch (e) { notify(e.message, 'error') }
  }

  if (!video) return <div className="muted">Cargando…</div>
  const running = job && ['queued', 'running'].includes(job.status)
  const shown = (clips || []).filter((c) => filter === 'all' || c.status === filter)
  const current = clips?.find((c) => c.id === selected)
  const counts = (clips || []).reduce((a, c) => ({ ...a, [c.status]: (a[c.status] || 0) + 1 }), {})

  return (
    <>
      <div className="panel analyze-panel">
        <div className="analyze-head">
          <div>
            <h2>{video.title}</h2>
            <div className="muted small">
              {fmtDuration(video.duration)} · {video.width}×{video.height}
              {state?.analysis && <> · Analizado {state.analysis.created_at} con {state.analysis.llm_model ? `IA (${state.analysis.llm_model})` : 'método heurístico'}</>}
            </div>
          </div>
          <div className="analyze-opts">
            <label>Clips <input type="number" min="1" max="30" value={opts.max_clips} onChange={(e) => setOpts({ ...opts, max_clips: +e.target.value })} /></label>
            <label>Mín. s <input type="number" min="5" max="180" value={opts.min_dur} onChange={(e) => setOpts({ ...opts, min_dur: +e.target.value })} /></label>
            <label>Máx. s <input type="number" min="10" max="300" value={opts.max_dur} onChange={(e) => setOpts({ ...opts, max_dur: +e.target.value })} /></label>
            <ModelPicker value={model} onChange={setModel} compact />
            <RolesPanel notify={notify} compact />
            <label className="check" title="Una IA distinta revisa gancho, retención y remate de cada clip, recorta a frase completa y reordena"><input type="checkbox" checked={opts.judge} onChange={(e) => setOpts({ ...opts, judge: e.target.checked })} /> Segunda opinión (juez)</label>
            <button className="btn btn-primary" disabled={running || !video.has_audio} onClick={() => start(false)}>
              <Icon name="spark" /> {state?.analysis ? 'Volver a analizar' : 'Analizar con IA'}
            </button>
          </div>
        </div>
        {!video.has_audio && <div className="alert alert-warn">Este video no tiene audio; no se puede transcribir.</div>}
        {job && job.status !== 'done' && <JobProgress job={job} />}
        {state?.has_transcript && !running && (
          <div className="muted small">La transcripción se reutiliza al volver a analizar. <a onClick={() => start(true)}>Transcribir de nuevo</a></div>
        )}
      </div>

      <div className="review-layout">
        <div className="cand-list panel">
          <div className="panel-head">
            <h2>Candidatos {clips ? `(${clips.length})` : ''}</h2>
            <button className="icon-btn" title="Crear clip manual" onClick={addManual}><Icon name="plus" /></button>
          </div>
          <div className="seg seg-full">
            {[['all', 'Todos'], ['pending', `Pend. ${counts.pending || 0}`], ['approved', `✓ ${counts.approved || 0}`], ['discarded', `✕ ${counts.discarded || 0}`]].map(([k, l]) => (
              <button key={k} className={filter === k ? 'on' : ''} onClick={() => setFilter(k)}>{l}</button>
            ))}
          </div>
          {clips?.length === 0 && !running && (
            <div className="empty small"><p>Sin candidatos todavía. Pulsa <b>Analizar con IA</b>.</p></div>
          )}
          <ul className="cands">
            {shown.map((c, i) => (
              <li key={c.id} className={`cand ${c.id === selected ? 'on' : ''} st-${c.status}`} onClick={() => setSelected(c.id)}>
                <ScoreRing value={c.score} />
                <div className="cand-body">
                  <div className="cand-title">{c.title}</div>
                  <div className="cand-sub">
                    {fmtTime(c.start_s)} – {fmtTime(c.end_s)} · {Math.round(c.duration)}s
                  </div>
                  <div className="cand-badges">
                    <span className={`badge src-${c.source}`}>{SOURCE_LABEL[c.source]}</span>
                    {c.status !== 'pending' && <span className={`badge stb-${c.status}`}>{STATUS_LABEL[c.status]}</span>}
                    {c.renders.length > 0 && <span className="badge badge-ok">{c.renders.length} render</span>}
                    {c.judge?.verdict && <span className={`badge jb-${c.judge.verdict}`}>{{ publicar: 'Juez: publicar', mejorar: 'Juez: mejorar', descartar: 'Juez: descartar' }[c.judge.verdict]}</span>}
                  </div>
                </div>
              </li>
            ))}
          </ul>
        </div>

        <div className="review-pane">
          {current
            ? <ClipReview key={current.id} clip={current} video={video} ai={ai} notify={notify}
                onChanged={async () => { await loadClips(); onChanged() }} />
            : <div className="panel empty"><p>Selecciona un candidato para revisarlo.</p></div>}
        </div>
      </div>
    </>
  )
}

export function ScoreRing({ value, size = 44 }) {
  if (value == null) return <div className="ring ring-none" style={{ width: size, height: size }}>—</div>
  const v = Math.round(value)
  const color = v >= 75 ? 'var(--ok)' : v >= 55 ? 'var(--warn)' : 'var(--muted)'
  return (
    <div className="ring" style={{ width: size, height: size, background: `conic-gradient(${color} ${v * 3.6}deg, var(--line) 0)` }}>
      <span>{v}</span>
    </div>
  )
}
