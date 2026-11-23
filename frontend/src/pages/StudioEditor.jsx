import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import Icon from '../components/Icon.jsx'
import JobProgress from '../components/JobProgress.jsx'
import Timeline from '../components/studio/Timeline.jsx'
import ModelPicker, { useModelChoice } from '../components/ModelPicker.jsx'
import Player from '../components/studio/Player.jsx'
import { LiveEngine, layoutOf } from '../components/studio/live.js'
import RolesPanel from '../components/RolesPanel.jsx'
import QaReport from '../components/QaReport.jsx'
import PublishComposer from '../components/PublishComposer.jsx'
import BriefForm, { EMPTY_BRIEF } from '../components/studio/BriefForm.jsx'
import { AiBox, AssetsPanel, Inspector, StylePanel } from '../components/studio/Panels.jsx'
import { clone, deleteItem, findItem } from '../components/studio/tl.js'
import { api, fmtDuration, fmtSize } from '../api.js'

const JOB_LABEL = { studio_autoedit: 'Auto-edición', studio_render: 'Render', studio_instruct: 'IA', studio_reference: 'Referencia', studio_topic: 'Video desde tema' }

export default function StudioEditor({ pid, notify }) {
  const [project, setProject] = useState(null)
  const [assetList, setAssetList] = useState([])
  const [llmReady, setLlmReady] = useState(false)
  const [selected, setSelected] = useState(null)
  const [playhead, setPlayhead] = useState(0)
  const [tab, setTab] = useState('ai')
  const [watch, setWatch] = useState([]) // [{id, kind, label}]
  const [jobs, setJobs] = useState({})
  const [aiLog, setAiLog] = useState([])
  const [finals, setFinals] = useState([])
  const [compose, setCompose] = useState(null)
  const [sound, setSound] = useState(null) // reporte del juez de sonido ('busy' mientras revisa)
  const [report, setReport] = useState(null) // reporte de la auto-edición / parecido al estilo
  const [briefOpen, setBriefOpen] = useState(null)
  const [name, setName] = useState('')
  const [videosById, setVideosById] = useState({})
  const [model, setModel] = useModelChoice()
  const [theater, setTheater] = useState(() => { try { return localStorage.getItem('smc_theater') === '1' } catch { return false } })
  const [tlHeight, setTlHeight] = useState(() => { try { return Number(localStorage.getItem('smc_tl_h')) || 300 } catch { return 300 } })
  const toggleTheater = useCallback(() => setTheater((x) => { try { localStorage.setItem('smc_theater', x ? '0' : '1') } catch { /* */ } return !x }), [])
  const startResize = (e) => {
    e.preventDefault()
    const y0 = e.clientY, h0 = tlHeight
    let h = h0
    const move = (ev) => { h = Math.max(150, Math.min(window.innerHeight * 0.7, h0 + (y0 - ev.clientY))); setTlHeight(h) }
    const up = () => { window.removeEventListener('mousemove', move); window.removeEventListener('mouseup', up); try { localStorage.setItem('smc_tl_h', String(Math.round(h))) } catch { /* */ } }
    window.addEventListener('mousemove', move); window.addEventListener('mouseup', up)
  }
  const video = useRef(null)
  const saveChain = useRef(Promise.resolve())
  const [view, setView] = useState(() => { try { return localStorage.getItem('smc_pv_view') || 'live' } catch { return 'live' } })
  const [reframe, setReframe] = useState(false)
  const setViewP = (v) => { setView(v); try { localStorage.setItem('smc_pv_view', v) } catch { /* */ } }
  const kind = project?.source_kind
  const engine = useMemo(() => (kind ? new LiveEngine({
    srcUrl: `/api/studio/projects/${pid}/source`, kind, proxy: () => api.studioProxy(pid),
  }) : null), [pid, kind])
  useEffect(() => () => engine?.destroy(), [engine])

  const assetsById = useMemo(() => Object.fromEntries(assetList.map((a) => [a.id, a])), [assetList])
  useEffect(() => { if (engine && project) engine.setProject(project.timeline, project.style, assetsById) }, [engine, project, assetsById])

  const loadAssets = useCallback(() => api.assets().then(setAssetList).catch(() => {}), [])
  const loadFinals = useCallback(() => api.studioRenders(pid).then((r) => setFinals(r.filter((x) => x.quality === 'final'))).catch(() => {}), [pid])
  const load = useCallback(async () => {
    const p = await api.studioProject(pid)
    setProject(p)
    setName(p.name)
    return p
  }, [pid])

  useEffect(() => {
    load().catch((e) => notify(e.message, 'error'))
    loadAssets(); loadFinals()
    api.library().then((d) => setVideosById(Object.fromEntries(d.items.map((v) => [v.id, v])))).catch(() => {})
    api.studioStatus().then((s) => setLlmReady(s.llm_ready)).catch(() => {})
    api.studioJobs(pid).then((js) => setWatch(js.map((j) => ({ id: j.id, kind: j.kind })))).catch(() => {})
  }, [pid, load, loadAssets, loadFinals, notify])

  const startJob = (job, extra = {}) => setWatch((w) => [...w, { id: job.id, kind: job.kind, ...extra }])

  const render = useCallback(async (quality) => {
    try { startJob(await api.studioRender(pid, quality)) } catch (e) { notify(e.message, 'error') }
  }, [pid, notify])

  // Seguimiento de trabajos
  useEffect(() => {
    if (!watch.length) return
    let alive = true
    const t = setInterval(async () => {
      for (const w of watch) {
        let j
        try { j = await api.job(w.id) } catch { continue }
        if (!alive) return
        setJobs((s) => ({ ...s, [w.id]: j }))
        if (!['done', 'error', 'cancelled'].includes(j.status)) continue
        setWatch((ws) => ws.filter((x) => x.id !== w.id))
        if (j.status === 'error') notify(`${JOB_LABEL[j.kind]}: ${j.error}`, 'error')
        if (w.kind === 'studio_instruct') {
          setAiLog((log) => log.map((l) => (l.job === w.id ? { ...l, a: j.status === 'done' ? (j.result.message || 'Hecho') : j.error, error: j.status !== 'done', skipped: j.result?.skipped } : l)))
        }
        if (j.status !== 'done') continue
        if (w.kind === 'studio_render') { await load(); loadFinals(); if (j.result.quality === 'final') notify('Video final listo para descargar') }
        else {
          await load()
          if (w.kind === 'studio_autoedit') {
            const r = j.result
            notify(`Edición lista: ${r.segments} cortes, ${r.removed_seconds}s de silencios y muletillas eliminados`)
            if (r.ai?.generative || r.ai?.sound || r.ai?.instructions) setReport({ ai: r.ai })
            if (view === 'render') render('preview')
          }
          if (w.kind === 'studio_reference') notify(`Ritmo aplicado: ${j.result.cuts_per_min} cortes/min. Pulsa “Auto-editar de nuevo” para usarlo.`)
        }
      }
    }, 1300)
    return () => { alive = false; clearInterval(t) }
  }, [watch, load, loadFinals, notify, render, view])

  const commit = useCallback((tl, note) => {
    setProject((p) => ({ ...p, timeline: tl }))
    saveChain.current = saveChain.current.then(() =>
      api.studioSaveTimeline(pid, tl, note).then(setProject).catch((e) => { notify(e.message, 'error'); load() }))
  }, [pid, notify, load])

  const saveStyle = useCallback(async (patch) => {
    setProject((p) => ({ ...p, style: { ...p.style, ...patch } }))
    try { setProject(await api.studioSaveStyle(pid, { ...project.style, ...patch })) } catch (e) { notify(e.message, 'error'); load() }
  }, [pid, project, notify, load])

  const undo = useCallback(async () => setProject(await api.studioUndo(pid)), [pid])
  const redo = useCallback(async () => setProject(await api.studioRedo(pid)), [pid])
  const split = useCallback(async (t) => {
    try { setProject(await api.studioSplit(pid, t)) } catch (e) { notify(e.message, 'error') }
  }, [pid, notify])

  // Atajos de teclado
  useEffect(() => {
    const onKey = (e) => {
      if (['INPUT', 'TEXTAREA', 'SELECT'].includes(e.target.tagName)) return
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'z') { e.preventDefault(); e.shiftKey ? redo() : undo() }
      else if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'y') { e.preventDefault(); redo() }
      else if ((e.key === 'Delete' || e.key === 'Backspace') && selected && project) {
        commit(deleteItem(clone(project.timeline), selected), 'Eliminar elemento'); setSelected(null)
      } else if (e.key.toLowerCase() === 's' && !e.ctrlKey) split(playhead)
      else if (video.current) {
        const P = video.current, k = e.key
        if (k === ' ' || k.toLowerCase() === 'k') { e.preventDefault(); P.toggle() }
        else if (k === 'ArrowLeft') { e.preventDefault(); P.seek(P.currentTime - (e.shiftKey ? 5 : 1)) }
        else if (k === 'ArrowRight') { e.preventDefault(); P.seek(P.currentTime + (e.shiftKey ? 5 : 1)) }
        else if (k.toLowerCase() === 'j') P.seek(P.currentTime - 5)
        else if (k.toLowerCase() === 'l') P.seek(P.currentTime + 5)
        else if (k === ',') P.step(-1)
        else if (k === '.') P.step(1)
        else if (k === 'ArrowUp') { e.preventDefault(); P.jumpCut(-1) }
        else if (k === 'ArrowDown') { e.preventDefault(); P.jumpCut(1) }
        else if (k === 'Home') P.seek(0)
        else if (k === 'End') P.seek(1e9)
        else if (k.toLowerCase() === 'f') P.fullscreen()
        else if (k.toLowerCase() === 'm') P.mute()
        else if (k.toLowerCase() === 't') toggleTheater()
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [selected, project, playhead, commit, undo, redo, split, toggleTheater])

  if (!project) return <div className="page"><div className="muted">Cargando proyecto…</div></div>

  const tl = project.timeline
  const lr = project.last_render
  const stale = !lr || lr.timeline_hash !== project.timeline_hash
  const activeJobs = watch.map((w) => jobs[w.id]).filter(Boolean)
  const busy = (kind) => watch.some((w) => w.kind === kind)
  const vertical = tl.height > tl.width

  const seek = (t) => {
    setPlayhead(t)
    if (video.current) video.current.seek(t)
  }
  const cuts = tl.tracks.video.slice(1).map((x) => x.start)
  const chapters = tl.tracks.text.filter((x) => x.kind === 'lower').map((x) => ({ start: Math.max(0, x.start - 0.3), text: x.text }))

  const reAutoedit = async () => {
    try { setBriefOpen((await fetch(`/api/studio/projects/${pid}/brief`).then((r) => r.json())).saved) } catch { setBriefOpen({ ...EMPTY_BRIEF }) }
  }
  const runAutoedit = async () => {
    const b = briefOpen
    setBriefOpen(null)
    try { startJob(await api.studioAutoedit(pid, { use_ai: true, llm_model: model || null, brief: b })) } catch (e) { notify(e.message, 'error') }
  }

  const judgeSound = async () => {
    setSound('busy')
    try {
      const r = await fetch(`/api/studio/projects/${pid}/sound-judge`, { method: 'POST' })
      const j = await r.json()
      if (!r.ok) throw new Error(j.detail || 'No se pudo revisar el sonido')
      if (j.project) setProject(j.project)
      setSound(j.report)
    } catch (e) { setSound(null); notify(e.message, 'error') }
  }

  const styleScore = async () => {
    try { setReport({ style: await fetch(`/api/studio/projects/${pid}/style-score`).then((r) => r.json()) }) } catch (e) { notify(e.message, 'error') }
  }

  const rename = async () => {
    if (name.trim() && name !== project.name) {
      try { setProject({ ...project, ...(await api.studioRename(pid, name.trim())) }) } catch (e) { notify(e.message, 'error') }
    }
  }

  return (
    <div className="studio">
      <header className="studio-head">
        <a className="icon-btn" href="#/studio" title="Volver a proyectos">←</a>
        <input className="studio-name" value={name} onChange={(e) => setName(e.target.value)} onBlur={rename} />
        <span className="muted small">{project.source_kind === 'audio' ? 'Voz en off' : 'Video'} · {fmtDuration(tl.duration)} · {tl.width}×{tl.height}</span>
        <div className="studio-actions">
          <button className="icon-btn" title="Deshacer (Ctrl+Z)" disabled={!project.can_undo} onClick={undo}>↶</button>
          <button className="icon-btn" title="Rehacer (Ctrl+Y)" disabled={!project.can_redo} onClick={redo}>↷</button>
          <RolesPanel notify={notify} compact />
          <ModelPicker value={model} onChange={setModel} compact />
          <button className="btn" onClick={reAutoedit} disabled={busy('studio_autoedit')} title="Rehace la edición con el modelo elegido"><Icon name="spark" /> Auto-editar de nuevo</button>
          <a className="btn" href={`/api/studio/projects/${pid}/export/resolve`} title="Descarga el proyecto para seguir editándolo en DaVinci Resolve (FCPXML + subtítulos)">
            <Icon name="film" /> DaVinci Resolve</a>
          <button className="btn btn-primary" onClick={() => render('final')} disabled={busy('studio_render')} title="Máxima calidad: 1440p, 60 fps si tu grabación lo es"><Icon name="upload" /> Exportar video</button>
        </div>
      </header>

      {activeJobs.length > 0 && (
        <div className="studio-jobs">{activeJobs.map((j) => <JobProgress key={j.id} job={j} compact />)}</div>
      )}

      <div className={`studio-main ${theater ? 'is-theater' : ''}`}>
        <div className="studio-preview">
          <div className={`pv-player ${vertical ? 'is-vertical' : ''}`}>
          <Player ref={video} engine={view === 'live' ? engine : null} src={view === 'render' && lr?.available ? lr.url : null}
            fps={tl.fps} cuts={cuts} chapters={chapters}
            onTime={setPlayhead} theater={theater} onTheater={toggleTheater}
            overlay={view === 'live' && reframe ? <ReframeLayer tl={tl} playhead={playhead} engine={engine} style={project.style} assetsById={assetsById} onCommit={commit} onDone={() => setReframe(false)} />
              : view === 'live' && selected ? <MoveLayer tl={tl} selected={selected} playhead={playhead} engine={engine} onCommit={commit} onSeek={seek} /> : null}
            badge={view === 'render' && stale && lr?.available && !busy('studio_render') ? 'Vista previa desactualizada: actualízala para ver tus cambios' : null}
            empty={busy('studio_autoedit') ? 'La IA está editando tu toma…' : busy('studio_render') ? 'Renderizando la vista previa…' : 'Sin vista previa renderizada: usa «En vivo» o actualízala'} />
          </div>
          <div className="pv-actions">
            <div className="seg pv-view">
              <button className={view === 'live' ? 'on' : ''} onClick={() => setViewP('live')} title="Ves tus cambios al instante, sin renderizar">● En vivo</button>
              <button className={view === 'render' ? 'on' : ''} onClick={() => setViewP('render')} title="El video renderizado, tal como se exportará">Render</button>
            </div>
            {view === 'live' && project.source_kind === 'video' && (
              <button className={`btn ${reframe ? 'btn-primary' : ''}`} onClick={() => setReframe(!reframe)}
                title="Arrastra sobre el video para mover el encuadre de la toma actual">✥ {reframe ? 'Listo' : 'Mover encuadre'}</button>
            )}
            {view === 'render' && (<>
            <button className="btn" onClick={() => render('preview')} disabled={busy('studio_render') || busy('studio_autoedit')}
              title="Rápida, 720p">
              {busy('studio_render') ? 'Renderizando…' : stale ? '↻ Actualizar vista previa (720p)' : '✓ Vista previa al día'}
            </button>
            <button className="btn" onClick={() => render('hd')} disabled={busy('studio_render') || busy('studio_autoedit')}
              title="Calidad completa para revisar detalles">Vista previa HD 1080p</button>
            </>)}
            {view === 'live' && <span className="muted small">La vista en vivo es aproximada (sin transiciones complejas ni voz realzada); exporta para el resultado exacto.</span>}
            {view === 'render' && lr && <span className="muted small">{lr.quality === 'final' ? 'Mostrando: video final' : 'Mostrando: vista previa'}</span>}
            <button className="btn" onClick={judgeSound} disabled={sound === 'busy' || busy('studio_autoedit')}
              title="Un juez revisa cada efecto de sonido: quita los puestos al azar o fuera de estilo y ajusta volúmenes (compara con tus referencias)">
              {sound === 'busy' ? '🎧 Revisando sonido…' : '🎧 Juez de sonido'}</button>
            <button className="btn" onClick={styleScore} title="Qué tanto se parece tu edición al estilo elegido (referencia o tipo de video)">🎯 Parecido al estilo</button>
            {lr?.qa && <QaReport qa={lr.qa} audio={lr.audio} onSeek={seek} />}
            {finals.length > 0 && (
              <details className="finals-pop">
                <summary className="btn sm">Exportados ({finals.length})</summary>
                <div className="finals-list">
                  {finals.map((f) => (
                    <div key={f.id} className="final-item">
                      <span className="small">{f.created_at}</span>
                      <span className="muted small">{fmtSize(f.size_bytes)}</span>
                      {f.qa && <span className={`badge ${f.qa.verdict === 'listo' ? 'badge-ok' : 'badge-warn'}`}>QA {f.qa.score}</span>}
                      {f.available && <button className="btn sm btn-primary" onClick={() => setCompose({ kind: 'studio_render', id: f.id })}>Publicar</button>}
                      {f.available && <a className="btn sm" href={`${f.url}?download=true`}>Descargar</a>}
                    </div>
                  ))}
                </div>
              </details>
            )}
            {report && (() => {
              const g = report.ai?.generative, st = report.style || g
              const dims = st?.dims || []
              return (
                <div className="modal-backdrop" onClick={() => setReport(null)}>
                  <div className="modal sj-modal" onClick={(e) => e.stopPropagation()}>
                    <h3>🎯 Reporte de edición {st?.score != null && <span className={`badge ${st.score >= 80 ? 'badge-ok' : 'badge-warn'}`}>{Math.round(st.score)} % de parecido</span>}</h3>
                    {st?.reference && <p className="muted small">Comparado con «{st.reference}». Meta: 80 % (adaptado a tu contenido).</p>}
                    {g?.error && <p className="error-text small">Edición generativa: {g.error}</p>}
                    {g && !g.error && (
                      <p className="small">Recursos: {g.found} de {g.planned} momentos planeados · {g.web} nuevos de internet · {g.reused} reutilizados de tu índice · modo {g.mode} · {g.rounds?.length} rondas del juez{g.reached ? ' · ✓ meta alcanzada' : ''}.</p>
                    )}
                    {dims.length > 0 && (
                      <table className="sj-table"><thead><tr><th>Aspecto</th><th>Tu edición</th><th>Referencia</th><th>%</th></tr></thead>
                        <tbody>{dims.map((d) => <tr key={d.key}><td>{d.label_es}</td><td>{String(d.mine ?? '—')}</td><td>{String(d.ref ?? '—')}</td><td>{d.score}</td></tr>)}</tbody></table>
                    )}
                    {(st?.advice || []).length > 0 && <p className="muted small">Sugerencias: {(st.advice || []).slice(0, 3).map((a) => a.why_es).join(' · ')}</p>}
                    {report.ai?.condense && (() => {
                      const c = report.ai.condense
                      const f = (x) => `${Math.floor(x / 60)}:${String(Math.round(x % 60)).padStart(2, '0')}`
                      return <p className="small">⏱️ Duración: {c.removed_blocks ? `${f(c.before)} → ${f(c.after)} (pediste ${f(c.target)}; quité ${c.removed_blocks} de ${c.blocks} partes, elegidas por ${c.method})` : `${f(c.before)}: ${c.note || 'sin recortar'}`}</p>
                    })()}
                    {report.ai?.sound && !report.ai.sound.error && <p className="small">🎧 Sonido: {report.ai.sound.summary}</p>}
                    {report.ai?.instructions?.error && <p className="error-text small">Tus instrucciones no se pudieron aplicar: {report.ai.instructions.error}</p>}
                    {report.ai?.instructions?.message && <p className="small">📝 Instrucciones: {report.ai.instructions.message}</p>}
                    <div className="row-end"><button className="btn btn-primary" onClick={() => setReport(null)}>Listo</button></div>
                  </div>
                </div>
              )
            })()}
            {sound && sound !== 'busy' && (
              <div className="modal-backdrop" onClick={() => setSound(null)}>
                <div className="modal sj-modal" onClick={(e) => e.stopPropagation()}>
                  <h3>🎧 Juez de sonido <span className="badge">{sound.score ?? '—'}/10</span></h3>
                  <p className="muted">{sound.summary}{sound.listened ? ' · Escuché tu último render.' : ''}{sound.llm ? ' · Revisado con IA.' : ''}</p>
                  {sound.target_per_min != null && <p className="small">Objetivo: ~{sound.target_per_min} efectos por minuto, al menos {sound.min_gap_s} s entre uno y otro{sound.reference ? ` (según «${sound.reference.name}»)` : ''}.</p>}
                  {sound.compare?.length > 0 && (
                    <table className="sj-table"><thead><tr><th>Comparado con tu referencia</th><th>Tu edición</th><th>Referencia</th></tr></thead>
                      <tbody>{sound.compare.map((c) => <tr key={c.que}><td>{c.que}</td><td>{String(c.tuyo)}</td><td>{String(c.referencia)}</td></tr>)}</tbody></table>
                  )}
                  {sound.changes?.length > 0 ? (
                    <div className="sj-list">{sound.changes.map((c, i) => (
                      <button key={i} className="sj-row" onClick={() => seek(c.t)} title="Ir a ese momento">
                        <span className="mono small">{fmtDuration(c.t)}</span> <b>{c.accion}</b> <span className="small">{c.sonido}</span>
                        <span className="muted small"> — {c.motivo}</span></button>))}</div>
                  ) : <p>Todo bien: no hubo nada que corregir.</p>}
                  <p className="muted small">Los cambios ya están aplicados; puedes deshacerlos con ↶.</p>
                  <div className="row-end"><button className="btn btn-primary" onClick={() => setSound(null)}>Listo</button></div>
                </div>
              </div>
            )}
            {briefOpen && (
              <div className="modal-backdrop" onClick={() => setBriefOpen(null)}>
                <div className="modal modal-form brief-modal" onClick={(e) => e.stopPropagation()}>
                  <button className="icon-btn modal-close" onClick={() => setBriefOpen(null)}><Icon name="x" /></button>
                  <h2>Auto-editar de nuevo</h2>
                  <p className="muted small">Se rehace la edición desde tu toma original siguiendo esto. Tus cambios manuales se reemplazan (puedes deshacer); la pantalla verde que ajustaste a mano se conserva.</p>
                  <BriefForm value={briefOpen} onChange={setBriefOpen} format={project.format} />
                  <button className="btn btn-primary full mt" onClick={runAutoedit}><Icon name="spark" /> Auto-editar</button>
                </div>
              </div>
            )}
            {compose && <PublishComposer source={compose} notify={notify} navigate={(pg) => { window.location.hash = `#/${pg}` }} onClose={() => setCompose(null)} />}
          </div>
        </div>

        <aside className="studio-side panel">
          <div className="seg seg-full">
            {[['ai', 'IA'], ['inspector', 'Editar'], ['assets', 'Recursos'], ['style', 'Estilo']].map(([k, l]) => (
              <button key={k} className={tab === k ? 'on' : ''} onClick={() => setTab(k)}>{l}</button>
            ))}
          </div>
          <div className="side-body">
            {tab === 'ai' && <AiBox project={project} notify={notify} log={aiLog} llmReady={llmReady}
              onJob={(job, q) => { startJob(job); setAiLog((l) => [{ job: job.id, q }, ...l]) }} />}
            {tab === 'inspector' && <Inspector projectId={project.id} tl={tl} selected={selected} assetsById={assetsById} videosById={videosById} sourceKind={project.source_kind}
              sourceDuration={project.source_meta.duration} playhead={playhead} onCommit={commit} onSplit={split} onSelect={setSelected}
              style={project.style} onStyle={saveStyle} />}
            {tab === 'assets' && <AssetsPanel tl={tl} playhead={playhead} onCommit={commit} notify={notify}
              onAssetsChanged={loadAssets} settings={project.style} />}
            {tab === 'style' && <StylePanel project={project} notify={notify} onSaved={setProject} onJob={startJob} />}
          </div>
        </aside>
      </div>

      <div className="tl-resize" onMouseDown={startResize} title="Arrastra para cambiar el tamaño del timeline" />
      <Timeline height={tlHeight} tl={tl} assetsById={assetsById} videosById={videosById} sourceDuration={project.source_meta.duration} playhead={playhead}
        onSeek={seek} selected={selected} onSelect={(id) => { setSelected(id); if (id) setTab('inspector') }}
        onCommit={commit} maxWords={project.style.caption_max_words} onSplit={split}
        onDelete={() => { if (selected) { commit(deleteItem(clone(tl), selected), 'Eliminar elemento'); setSelected(null) } }} />
    </div>
  )
}


/**
 * Mover y redimensionar libremente sobre la vista previa el elemento seleccionado: tarjetas/ventanas de recursos
 * y textos. Arrastra la caja para moverla; la esquina (o la rueda del ratón) cambia el tamaño. Doble clic = posición
 * automática otra vez. Sirve igual para videos largos y clips.
 */
function MoveLayer({ tl, selected, playhead, engine, onCommit, onSeek }) {
  const layer = useRef(null)
  const drag = useRef(null)
  const [, force] = useState(0)
  const { track, item } = findItem(tl, selected)
  const movable = item && ((track === 'broll' && item.mode && item.mode !== 'full') || track === 'text')
  useEffect(() => { // la caja se mide después de que el motor dibuja el cuadro
    if (!movable) return
    let id = requestAnimationFrame(() => { id = requestAnimationFrame(() => force((x) => x + 1)) })
    return () => cancelAnimationFrame(id)
  }, [movable, playhead, tl, selected])
  useEffect(() => {
    if (!movable) return
    const r = () => force((x) => x + 1)
    window.addEventListener('resize', r)
    return () => window.removeEventListener('resize', r)
  }, [movable])
  if (!movable || !engine?.root) return null
  const visible = playhead >= item.start && playhead < item.end
  if (!visible) {
    return (
      <div className="move-layer is-off">
        <button className="btn sm move-goto" onClick={(e) => { e.stopPropagation(); onSeek(item.start + Math.min(0.4, (item.end - item.start) / 2)) }}>
          ✥ Ir a «{track === 'text' ? item.text : 'recurso'}» para moverlo
        </button>
      </div>
    )
  }
  const R = engine.root.getBoundingClientRect()
  const L = layer.current?.getBoundingClientRect()
  const box = engine.itemBox(selected)
  const k = R.width / tl.width // px de pantalla por px del timeline
  const off = L ? { left: R.left - L.left, top: R.top - L.top } : { left: 0, top: 0 }

  const begin = (e, mode) => {
    e.preventDefault(); e.stopPropagation()
    if (!box) return
    // tarjetas: pos = centro del marco (default del motor); textos: centro de la caja que se ve
    const def = track === 'broll' ? (item.mode === 'pip' ? { x: 0.8, y: 0.26 } : { x: 0.68, y: 0.45 })
      : { x: (box.x + box.w / 2) / tl.width, y: (box.y + box.h / 2) / tl.height }
    const base = item.pos ? { x: Number(item.pos.x ?? def.x), y: Number(item.pos.y ?? def.y) } : def
    drag.current = { mode, x0: e.clientX, y0: e.clientY, base,
      s0: Number(item.scale) || (track === 'broll' ? (item.mode === 'pip' ? 0.3 : 0.42) : 1), w0: box.w * k, n: null }
    const move = (ev) => {
      const d = drag.current
      const n = clone(tl)
      const it = findItem(n, selected).item
      if (d.mode === 'move') {
        const x = Math.max(0, Math.min(1, d.base.x + (ev.clientX - d.x0) / R.width))
        const y = Math.max(0, Math.min(1, d.base.y + (ev.clientY - d.y0) / R.height))
        it.pos = { x: +x.toFixed(4), y: +y.toFixed(4) }
      } else {
        const f = Math.max(0.2, (d.w0 + (ev.clientX - d.x0) * 2) / Math.max(d.w0, 1))
        it.scale = +(track === 'broll' ? Math.max(0.1, Math.min(1, d.s0 * f)) : Math.max(0.3, Math.min(4, d.s0 * f))).toFixed(3)
      }
      d.n = n
      engine.setProject(n, engine.st, engine.assets)
    }
    const up = () => {
      window.removeEventListener('mousemove', move); window.removeEventListener('mouseup', up)
      const d = drag.current
      drag.current = null
      if (d?.n) onCommit(d.n, d.mode === 'move' ? 'Mover elemento' : 'Tamaño del elemento')
    }
    window.addEventListener('mousemove', move); window.addEventListener('mouseup', up)
  }
  const wheel = (e) => {
    e.stopPropagation()
    const n = clone(tl)
    const it = findItem(n, selected).item
    const s0 = Number(it.scale) || (track === 'broll' ? (it.mode === 'pip' ? 0.3 : 0.42) : 1)
    const f = e.deltaY < 0 ? 1.06 : 1 / 1.06
    it.scale = +(track === 'broll' ? Math.max(0.1, Math.min(1, s0 * f)) : Math.max(0.3, Math.min(4, s0 * f))).toFixed(3)
    onCommit(n, 'Tamaño del elemento')
  }
  const reset = (e) => {
    e.stopPropagation()
    const n = clone(tl)
    const it = findItem(n, selected).item
    if (track === 'text') { delete it.pos; delete it.scale } else { delete it.scale }
    onCommit(n, 'Posición automática')
  }
  return (
    <div ref={layer} className="move-layer">
      {box && (
        <div className="move-box" style={{ left: off.left + box.x * k, top: off.top + box.y * k, width: box.w * k, height: box.h * k }}
          onMouseDown={(e) => begin(e, 'move')} onWheel={wheel} onDoubleClick={reset} onClick={(e) => e.stopPropagation()}
          title="Arrastra para mover · esquina o rueda para cambiar el tamaño · doble clic: posición automática">
          <span className="move-handle" onMouseDown={(e) => begin(e, 'scale')} />
        </div>
      )}
    </div>
  )
}

/** Capa para mover el encuadre arrastrando sobre el video (toma actual; en modo stream, el juego). */
function ReframeLayer({ tl, playhead, engine, onCommit, onDone }) {
  const lay = layoutOf(tl)
  const idx = Math.max(0, tl.tracks.video.findIndex((s) => playhead < s.start + (s.src_out - s.src_in)))
  const seg = tl.tracks.video[idx]
  const [all, setAll] = useState(false)
  const drag = useRef(null)
  if (!seg) return null
  const split = lay.mode === 'split' && !seg.rect
  const canMove = split || (!seg.rect && (lay.mode === 'crop' || (seg.zoom || 1) > 1))
  const down = (e) => {
    if (!canMove) return
    e.preventDefault(); e.stopPropagation()
    const r = e.currentTarget.getBoundingClientRect()
    drag.current = { x0: e.clientX, w: r.width, base: split ? (lay.game_x ?? 0.5) : (seg.crop_x ?? 0.5), tl: null }
    const move = (ev) => {
      const d = drag.current
      const v = Math.max(0, Math.min(1, d.base - (ev.clientX - d.x0) / d.w * 0.9))
      const n = JSON.parse(JSON.stringify(tl))
      if (split) n.layout = { ...lay, game_x: +v.toFixed(3) }
      else (all ? n.tracks.video : [n.tracks.video[idx]]).forEach((s) => { s.crop_x = +v.toFixed(3); delete s.track })
      d.tl = n
      engine?.setProject(n, engine.st, engine.assets)
    }
    const up = () => {
      window.removeEventListener('mousemove', move); window.removeEventListener('mouseup', up)
      if (drag.current?.tl) onCommit(drag.current.tl, split ? 'Encuadre del juego' : 'Encuadre')
      drag.current = null
    }
    window.addEventListener('mousemove', move); window.addEventListener('mouseup', up)
  }
  return (
    <div className={`reframe-layer ${canMove ? '' : 'is-off'}`} onMouseDown={down} onClick={(e) => e.stopPropagation()}>
      <div className="reframe-tip" onMouseDown={(e) => e.stopPropagation()}>
        {canMove ? (split ? 'Arrastra a los lados para mover el juego' : 'Arrastra a los lados para mover el encuadre') : 'Este encuadre muestra la toma completa: sube el zoom de la toma para poder moverla'}
        {!split && canMove && <label className="check"><input type="checkbox" checked={all} onChange={(e) => setAll(e.target.checked)} /> todas las tomas</label>}
        <button className="btn sm" onClick={onDone}>Listo</button>
      </div>
    </div>
  )
}
