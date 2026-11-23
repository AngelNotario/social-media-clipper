import Feedback from './Feedback.jsx'
import { useEffect, useRef, useState } from 'react'
import PublishComposer from './PublishComposer.jsx'
import QaReport from './QaReport.jsx'
import JudgePanel from './JudgePanel.jsx'
import Icon from './Icon.jsx'
import JobProgress from './JobProgress.jsx'
import { api, fmtSize, fmtTime } from '../api.js'
import { useJob } from '../hooks.js'

const WHY_GOOD = ['Gran gancho', 'Muy gracioso', 'Buen remate', 'Se entiende solo', 'Duración justa', 'Buena edición']
const WHY_BAD = ['Gancho flojo', 'Muy largo', 'Muy corto', 'No se entiende solo', 'Corta a mitad', 'Aburrido / sin emoción', 'Mucho silencio', 'Demasiados efectos']

const CROP_OPTIONS = [
  ['face', 'Seguir rostro (auto)'],
  ['center', 'Recorte al centro'],
  ['blur', 'Video completo + fondo desenfocado'],
  ['fit', 'Video completo + barras negras'],
  ['stream', 'Stream: cámara arriba + juego abajo (híbrido)'],
]

export default function ClipReview({ clip, video, ai, notify, onChanged }) {
  const [compose, setCompose] = useState(null)
  const vref = useRef(null)
  const [start, setStart] = useState(clip.start_s)
  const [end, setEnd] = useState(clip.end_s)
  const [title, setTitle] = useState(clip.title)
  const [notes, setNotes] = useState(clip.notes || '')
  const [now, setNow] = useState(clip.start_s)
  const [segMode, setSegMode] = useState(false)
  const [saving, setSaving] = useState(false)
  const [renderJobId, setRenderJobId] = useState(null)
  const [learned, setLearned] = useState([])
  useEffect(() => { fetch('/api/studio/styles').then((r) => r.json()).then(setLearned).catch(() => {}) }, [])
  useEffect(() => {  // grabaciones de stream con cámara marcada → encuadre «Stream» por defecto
    fetch(`/api/stream/videos/${video.id}`).then((r) => r.json())
      .then((s) => { if (s.facecam && s.cam_mode !== 'never') setRo((o) => ({ ...o, crop_mode: 'stream' })) }).catch(() => {})
  }, [video.id])
  const [ro, setRo] = useState({
    crop_mode: 'face', subtitles: true, sub_style: 'karaoke', sub_position: 'auto',
    show_title: false, viral: true, generative: true, pro_audio: true, clean_voice: false, voice_mode: 'original', music: false, beat_sync: true, title: clip.judge?.hook_text || clip.hook || clip.title,
  })

  const dirty = start !== clip.start_s || end !== clip.end_s || title !== clip.title || notes !== (clip.notes || '')
  const dur = video.duration || 0

  useEffect(() => {
    const v = vref.current
    if (v) v.currentTime = clip.start_s
  }, [clip.start_s])

  const onTime = () => {
    const t = vref.current.currentTime
    setNow(t)
    if (segMode && t >= end) { vref.current.pause(); setSegMode(false) }
  }

  const playSegment = () => {
    const v = vref.current
    v.currentTime = start
    setSegMode(true)
    v.play()
  }
  const seek = (t) => { vref.current.currentTime = Math.max(0, Math.min(dur, t)) }
  const clamp = (x) => Math.round(Math.max(0, Math.min(dur, x)) * 10) / 10

  const save = async (extra = {}) => {
    setSaving(true)
    try {
      await api.updateClip(clip.id, { start_s: start, end_s: end, title, notes, ...extra })
      await onChanged()
      if (!extra.status) notify('Cambios guardados')
    } catch (e) { notify(e.message, 'error') } finally { setSaving(false) }
  }

  const setStatus = (status) => save({ status }).then(() =>  // el servidor guarda la foto del clip para tu perfil de gusto
    notify(status === 'approved' ? 'Clip aprobado · la IA aprende de esto' : status === 'discarded' ? 'Clip descartado · la IA aprende de esto' : 'Marcado como pendiente'))

  const [whySent, setWhySent] = useState([])
  const sendWhy = (w) => {
    setWhySent((x) => [...x, w])
    fetch('/api/feedback', { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ kind: 'clip', ref_id: clip.id, rating: clip.status === 'approved' ? 1 : -1, note: w,
        context: { title: clip.title, video: video.title } }) }).catch(() => {})
  }

  const remove = async () => {
    if (!window.confirm('¿Eliminar este clip y sus renders?')) return
    try { await api.deleteClip(clip.id); await onChanged(); notify('Clip eliminado') } catch (e) { notify(e.message, 'error') }
  }

  const renderJob = useJob(renderJobId, async (j) => {
    if (j.status === 'done') notify(`Clip renderizado (${j.result.encoder === 'h264_nvenc' ? 'GPU' : 'CPU'})`)
    else if (j.status === 'error') notify(j.error, 'error')
    await onChanged()
  })

  const doRender = async () => {
    try {
      if (dirty) await api.updateClip(clip.id, { start_s: start, end_s: end, title, notes })
      const j = await api.renderClip(clip.id, { ...ro, llm_model: localStorage.getItem('smc_llm_model') || null })
      setRenderJobId(j.id)
    } catch (e) { notify(e.message, 'error') }
  }

  const [editJobId, setEditJobId] = useState(null)
  const editJob = useJob(editJobId, async (j) => {
    if (j.status === 'done') {
      notify('Edición de la IA lista: ábrela y cambia lo que quieras')
      window.location.hash = `#/studio?p=${j.result.project_id}`
    } else if (j.status === 'error') notify(j.error, 'error')
  })
  const doEdit = async () => {
    try {
      if (dirty) await api.updateClip(clip.id, { start_s: start, end_s: end, title, notes })
      const j = await api.editClip(clip.id, { ...ro, llm_model: localStorage.getItem('smc_llm_model') || null })
      setEditJobId(j.id)
    } catch (e) { notify(e.message, 'error') }
  }
  const editing = editJob && ['queued', 'running'].includes(editJob.status)
  const rendering = renderJob && ['queued', 'running'].includes(renderJob.status)
  const pct = (t) => `${dur ? (t / dur) * 100 : 0}%`
  const scoreKeys = Object.keys(clip.scores || {})

  return (
    <div className="review">
      <div className="panel review-player">
        <div className="rp-video">
          <video ref={vref} src={video.stream_url} controls onTimeUpdate={onTime} onPause={() => setSegMode(false)} />
        </div>

        <div className="timeline" onClick={(e) => {
          const r = e.currentTarget.getBoundingClientRect()
          seek(((e.clientX - r.left) / r.width) * dur)
        }}>
          <div className="tl-seg" style={{ left: pct(start), width: pct(end - start) }} />
          <div className="tl-now" style={{ left: pct(now) }} />
        </div>

        <div className="trim">
          <div className="trim-box">
            <span className="muted small">Inicio</span>
            <div className="row">
              <button className="icon-btn sm" onClick={() => setStart(clamp(start - 0.5))}>−</button>
              <b className="mono">{fmtTime(start)}</b>
              <button className="icon-btn sm" onClick={() => setStart(clamp(Math.min(start + 0.5, end - 1)))}>+</button>
            </div>
            <button className="link-btn" onClick={() => setStart(clamp(Math.min(now, end - 1)))}>Marcar aquí</button>
          </div>
          <div className="trim-mid">
            <button className="btn btn-primary" onClick={playSegment}><Icon name="play" /> Reproducir clip</button>
            <span className="muted small">{(end - start).toFixed(1)} s</span>
          </div>
          <div className="trim-box">
            <span className="muted small">Fin</span>
            <div className="row">
              <button className="icon-btn sm" onClick={() => setEnd(clamp(Math.max(end - 0.5, start + 1)))}>−</button>
              <b className="mono">{fmtTime(end)}</b>
              <button className="icon-btn sm" onClick={() => setEnd(clamp(end + 0.5))}>+</button>
            </div>
            <button className="link-btn" onClick={() => setEnd(clamp(Math.max(now, start + 1)))}>Marcar aquí</button>
          </div>
        </div>
      </div>

      <div className="review-cols">
        <div className="panel">
          <label className="field-label">Título</label>
          <input className="input full" value={title} onChange={(e) => setTitle(e.target.value)} />

          {clip.text && (<>
            <label className="field-label mt">Transcripción del clip</label>
            <p className="transcript">{clip.text}</p>
            {(start !== clip.start_s || end !== clip.end_s) && <p className="muted small">Guarda para actualizar el texto con los nuevos tiempos.</p>}
          </>)}

          {clip.hook && (<><label className="field-label mt">Gancho</label><p className="quote">“{clip.hook}”</p></>)}
          {clip.reason && (<><label className="field-label mt">Por qué funciona</label><p className="small">{clip.reason}</p></>)}
          {clip.suggestions && (<><label className="field-label mt">Sugerencias de edición</label><p className="small">{clip.suggestions}</p></>)}

          <label className="field-label mt">Notas</label>
          <textarea className="input full" rows="2" value={notes} onChange={(e) => setNotes(e.target.value)} placeholder="Ideas, contexto a añadir…" />

          <div className="review-actions">
            <button className={`btn ${clip.status === 'approved' ? 'btn-ok' : ''}`} onClick={() => setStatus('approved')} disabled={saving}><Icon name="check" /> Aprobar</button>
            <button className={`btn ${clip.status === 'discarded' ? 'btn-danger' : ''}`} onClick={() => setStatus('discarded')} disabled={saving}><Icon name="x" /> Descartar</button>
            <button className="btn" onClick={() => save()} disabled={!dirty || saving}>Guardar cambios</button>
            <button className="icon-btn" title="Eliminar clip" onClick={remove}><Icon name="trash" /></button>
          </div>
          {clip.status !== 'pending' && (
            <div className="why">
              <span className="small">{clip.status === 'approved' ? '¿Qué te gustó? (la IA lo aprende)' : '¿Por qué lo descartaste? (la IA lo aprende)'}</span>
              <div className="why-chips">
                {(clip.status === 'approved' ? WHY_GOOD : WHY_BAD).map((w) => (
                  <button key={w} className={`chip ${whySent.includes(w) ? 'on' : ''}`} disabled={whySent.includes(w)} onClick={() => sendWhy(w)}>{w}</button>
                ))}
              </div>
            </div>
          )}
          <p className="muted small">Aprobar un clip no equivale a aprobar sus derechos de uso.</p>
          <Feedback key={clip.id} kind="clip" refId={clip.id} notify={notify} compact
            context={{ title: clip.title, video: video.title, hook: clip.hook }} />
        </div>

        <div className="side-col">
          <JudgePanel clip={clip} notify={notify} onChanged={() => onChanged()}
            onApplyTrim={(a, b) => { setStart(clamp(a)); setEnd(clamp(b)); notify('Recorte aplicado: pulsa “Guardar cambios” para conservarlo') }}
            onUseHook={(t) => { setRo((o) => ({ ...o, title: t, show_title: true })); notify('Texto de gancho listo en «Generar clip»') }} />
          {scoreKeys.length > 0 && (
            <div className="panel">
              <div className="panel-head">
                <h2>Puntuación editorial</h2>
                <b className="score-big">{clip.score != null ? Math.round(clip.score) : '—'}</b>
              </div>
              {scoreKeys.map((k) => (
                <div key={k} className="score-row">
                  <span>{ai?.criteria?.[k] || k}</span>
                  <div className="bar"><div className="bar-fill" style={{ width: `${clip.scores[k]}%` }} /></div>
                  <span className="mono small">{Math.round(clip.scores[k])}</span>
                </div>
              ))}
              <p className="muted small">Estimación editorial, no predicción de viralidad.</p>
            </div>
          )}

          <div className="panel">
            <div className="panel-head"><h2>Generar clip vertical 9:16</h2></div>
            <label className="field-label">Encuadre</label>
            <select className="input full" value={ro.crop_mode} onChange={(e) => setRo({ ...ro, crop_mode: e.target.value })}>
              {CROP_OPTIONS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
            </select>
            {learned.length > 0 && (<>
              <label className="field-label mt">Estilo de subtítulos y títulos</label>
              <select className="input full" value={ro.style_id || ''} onChange={(e) => setRo({ ...ro, style_id: e.target.value ? Number(e.target.value) : null })}>
                <option value="">Básico de la app</option>
                {learned.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
              </select>
            </>)}
            <label className="check" title="Golpes de zoom, sacudidas, textos meme y efectos de sonido decididos por la IA"><input type="checkbox" checked={ro.viral !== false} onChange={(e) => setRo({ ...ro, viral: e.target.checked })} /> Edición viral (zooms, memes y efectos)</label>
            <label className="check" title="La IA busca en internet logos, capturas, productos o clips de lo que dices, los verifica con visión y los pone como tarjetas animadas arriba de tu cara. Lo encontrado se guarda en «Recursos» y los demás clips del mismo video lo reutilizan (el primero tarda unos minutos más)."><input type="checkbox" checked={ro.generative !== false} onChange={(e) => setRo({ ...ro, generative: e.target.checked })} /> Recursos de internet (edición generativa)</label>
            <label className="check" title="Compresión de voz y volumen estándar de TikTok, Reels y Shorts (-14 LUFS)"><input type="checkbox" checked={ro.pro_audio !== false} onChange={(e) => setRo({ ...ro, pro_audio: e.target.checked })} /> Audio profesional (-14 LUFS)</label>
            {ro.pro_audio !== false && <label className="check" title="Úsalo si solo hay voz: quita ruido de fondo. En gameplay puede apagar sonidos del juego."><input type="checkbox" checked={!!ro.clean_voice} onChange={(e) => setRo({ ...ro, clean_voice: e.target.checked })} /> Quitar ruido de fondo (solo voz)</label>}
            <label className="field-label mt">Voz (la IA separa voz y música)</label>
            <select className="input full" value={ro.voice_mode || 'original'} onChange={(e) => setRo({ ...ro, voice_mode: e.target.value })}>
              <option value="original">Audio original</option>
              <option value="realzar">Realzar voz sobre el juego</option>
              <option value="solo_voz">Solo voz (quita música de fondo, evita reclamos)</option>
            </select>
            <label className="check" title="Música libre de derechos que baja sola cuando hablas"><input type="checkbox" checked={!!ro.music} onChange={(e) => setRo({ ...ro, music: e.target.checked })} /> Música de fondo (baja cuando hablas)</label>
            {ro.music && ro.viral !== false && <label className="check" title="Zooms, textos y efectos caen en el golpe de la música"><input type="checkbox" checked={ro.beat_sync !== false} onChange={(e) => setRo({ ...ro, beat_sync: e.target.checked })} /> Efectos al ritmo de la música</label>}
            <label className="check"><input type="checkbox" checked={ro.subtitles} onChange={(e) => setRo({ ...ro, subtitles: e.target.checked })} /> Subtítulos automáticos</label>
            {ro.subtitles && (
              <div className="row">
                <select className="input" value={ro.sub_style} onChange={(e) => setRo({ ...ro, sub_style: e.target.value })}>
                  <option value="karaoke">Palabra resaltada</option>
                  <option value="simple">Frases simples</option>
                </select>
                <select className="input" value={ro.sub_position} onChange={(e) => setRo({ ...ro, sub_position: e.target.value })}>
                  <option value="auto">Posición automática</option>
                  <option value="bottom">Abajo</option>
                  <option value="middle">Centro</option>
                  <option value="top">Arriba</option>
                </select>
              </div>
            )}
            <label className="check"><input type="checkbox" checked={ro.show_title} onChange={(e) => setRo({ ...ro, show_title: e.target.checked })} /> Título al inicio (3 s)</label>
            {ro.show_title && <input className="input full" value={ro.title} maxLength={120} onChange={(e) => setRo({ ...ro, title: e.target.value })} />}
            <button className="btn btn-primary full mt" onClick={doEdit} disabled={editing || rendering}
              title="La IA edita el clip y lo abre en el editor: lo ves al instante y cambias encuadre, subtítulos, zooms, textos y sonidos antes de exportar">
              {editing ? 'La IA está editando…' : '✎ Ver y editar antes de exportar'}
            </button>
            {editJob && editJob.status !== 'done' && <JobProgress job={editJob} compact />}
            <button className="btn full mt" onClick={doRender} disabled={rendering || editing}>
              {rendering ? 'Renderizando…' : 'Generar clip directo'}
            </button>
            {renderJob && renderJob.status !== 'done' && <JobProgress job={renderJob} compact />}

            {clip.renders.length > 0 && (
              <div className="renders">
                {clip.renders.map((r) => (
                  <div key={r.id} className="render-item">
                    {r.available ? <video src={r.url} controls preload="metadata" /> : <div className="muted small">Archivo no encontrado</div>}
                    <div className="render-meta">
                      <span className="small">{r.created_at}</span>
                      <span className="muted small">{fmtSize(r.size_bytes)} · {r.options.crop_used} · {r.encoder === 'h264_nvenc' ? 'GPU' : 'CPU'}</span>
                      {r.options.director && !r.options.director.error && <span className="muted small">{r.options.director.punch} zooms · {r.options.director.meme + r.options.director.callout} textos</span>}
                      {r.options.generative && (r.options.generative.error || r.options.generative.skipped
                        ? <span className="muted small">Recursos de internet: {r.options.generative.error || r.options.generative.skipped}</span>
                        : <span className="muted small">{r.options.generative.found ?? 0} recursos ({r.options.generative.web ?? 0} nuevos de internet, {r.options.generative.reused ?? 0} reutilizados){r.options.generative.score != null ? ` · ${Math.round(r.options.generative.score)} % de parecido al estilo` : ''}</span>)}
                      {(r.options.stems?.mode && r.options.stems.mode !== 'original') || r.options.director?.music ? <span className="muted small">{r.options.stems?.mode === 'realzar' ? 'Voz realzada' : r.options.stems?.mode === 'solo_voz' ? 'Solo voz' : ''}{r.options.director?.music ? ` · música: ${r.options.director.music}` : ''}{r.options.director?.beats?.tempo ? ` (${r.options.director.beats.tempo} BPM, ${r.options.director.beats.snapped} efectos al ritmo)` : ''}</span> : null}
                      {r.options.stems?.error && <span className="muted small">Sin separación de voz: {r.options.stems.error}</span>}
                      <QaReport qa={r.options.qa} audio={r.options.audio} />
                      <div className="row">
                        {r.available && <button className="btn sm btn-primary" onClick={() => setCompose({ kind: 'clip_render', id: r.id })}><Icon name="send" size={14} /> Publicar</button>}
                        {r.available && <a className="btn sm" href={`${r.url}?download=true`}><Icon name="upload" size={14} /> Descargar</a>}
                        <button className="btn sm" title="Eliminar este clip generado" onClick={async () => {
                          if (!window.confirm('¿Eliminar este clip generado? El archivo se borra del disco.')) return
                          try { await api.deleteRender(r.id); notify('Clip generado eliminado'); onChanged() } catch (e) { notify(e.message, 'error') }
                        }}><Icon name="trash" size={14} /> Eliminar</button>
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      </div>
      {compose && <PublishComposer source={compose} notify={notify} navigate={(pg) => { window.location.hash = `#/${pg}` }} onClose={() => setCompose(null)} />}
    </div>
  )
}
