import ModelPicker, { useModelChoice } from '../ModelPicker.jsx'
import { useEffect, useMemo, useRef, useState } from 'react'
import Icon from '../Icon.jsx'
import { api, fmtTime } from '../../api.js'
import { captionGroups, clone, deleteItem, ensureBgIds, findItem, relayout, segLen, trackList, uid } from './tl.js'

const num = (v, d = 2) => Math.round(v * 10 ** d) / 10 ** d

// ---------------- Inspector ----------------

const TRANS = [['cut', 'Corte directo'], ['zoom', 'Zoom de impacto'], ['flash', 'Destello blanco'], ['fade', 'Fundido'],
  ['glitch', 'Glitch'], ['whip', 'Barrido (whip)'], ['slide', 'Deslizar (solo b-roll)']]

const LAYOUTS = [
  ['crop', 'Recortado', 'Llena la pantalla vertical recortando los lados (sigue tu rostro)'],
  ['blur', 'Desenfocado', 'La toma completa con el fondo desenfocado'],
  ['fit', 'Barras', 'La toma completa con barras negras'],
  ['split', 'Stream', 'Tu cámara arriba y el juego abajo'],
]

const CK_PLACES = [['full', 'Completo', 1, 0.5, 1], ['bottom', 'Abajo', 0.75, 0.5, 1], ['left', 'Esquina izq.', 0.55, 0, 1],
  ['right', 'Esquina der.', 0.55, 1, 1]]

function ChromaBox({ tl, onCommit, videosById, assetsById, projectId }) {
  const ck = { enabled: false, color: '#00FF00', similarity: 0.14, blend: 0.08, despill: true, ...(tl.chroma || {}) }
  const bg = { kind: 'none', start: 0, volume: 0, color: '#101018', ...(ck.bg || {}) }
  const sub = { scale: 1, x: 0.5, y: 1, ...(ck.subject || {}) }
  const [busy, setBusy] = useState(false)
  const set = (patch, note = 'Pantalla verde') => {
    const n = clone(tl)
    n.chroma = { ...ck, ...patch, bg: { ...bg, ...(patch.bg || {}) }, subject: { ...sub, ...(patch.subject || {}) } }
    onCommit(n, note)
  }
  const detect = async (enable) => {
    setBusy(true)
    try {
      const r = await fetch(`/api/studio/projects/${projectId}/chroma/detect`, { method: 'POST' }).then((x) => x.json())
      set({ ...(enable ? { enabled: true } : {}), ...(r.detected ? { color: r.color, similarity: r.similarity, protect: r.protect || [] } : {}) }, 'Color de la pantalla verde')
    } catch { if (enable) set({ enabled: true }) }
    setBusy(false)
  }
  const videos = Object.values(videosById)
  const bgAssets = Object.values(assetsById).filter((a) => a.kind === 'broll' && a.available !== false)
  const bgVal = bg.kind === 'video' ? `v${bg.video_id}` : bg.kind === 'asset' ? `a${bg.asset_id}` : bg.kind === 'color' ? 'color' : 'none'
  return (
    <div className="insp-box">
      <div className="insp-sub">Pantalla verde (chroma key)</div>
      <label className="check"><input type="checkbox" checked={!!ck.enabled} disabled={busy}
        onChange={(e) => (e.target.checked ? (tl.chroma ? set({ enabled: true }) : detect(true)) : set({ enabled: false }))} /> Quitar el fondo verde</label>
      {ck.enabled && (<>
        <label className="insp-field">Fondo
          <select className="input" value={bgVal} onChange={(e) => {
            const v = e.target.value
            if (v[0] === 'v') set({ bg: { kind: 'video', video_id: +v.slice(1), asset_id: null } }, 'Fondo de la pantalla verde')
            else if (v[0] === 'a') set({ bg: { kind: 'asset', asset_id: +v.slice(1), video_id: null } }, 'Fondo de la pantalla verde')
            else set({ bg: { kind: v } }, 'Fondo de la pantalla verde')
          }}>
            <option value="none">Negro</option>
            <option value="color">Color sólido</option>
            {videos.length > 0 && <optgroup label="Videos de tu biblioteca (gameplay)">{videos.map((v) => <option key={v.id} value={`v${v.id}`}>{v.title}</option>)}</optgroup>}
            {bgAssets.length > 0 && <optgroup label="Recursos de b-roll">{bgAssets.map((a) => <option key={a.id} value={`a${a.id}`}>{a.title}</option>)}</optgroup>}
          </select>
        </label>
        {bg.kind === 'color' && <label className="insp-field">Color de fondo<input type="color" className="input" value={bg.color} onChange={(e) => set({ bg: { color: e.target.value } })} /></label>}
        {(bg.kind === 'video' || bg.kind === 'asset') && (
          <div className="insp-row">
            <label className="insp-field">Empieza en (s)
              <input type="number" className="input" min="0" step="1" defaultValue={bg.start} key={`s${bgVal}`} onBlur={(e) => set({ bg: { start: Math.max(0, +e.target.value || 0) } }, 'Inicio del fondo')} />
            </label>
            <label className="insp-field">Sonido del fondo ({Math.round(bg.volume * 100)}%)
              <input type="range" min="0" max="0.6" step="0.05" defaultValue={bg.volume} onMouseUp={(e) => set({ bg: { volume: +e.target.value } }, 'Sonido del fondo')} />
            </label>
          </div>
        )}
        <div className="insp-field">Tú sobre el fondo
          <div className="lay-picks">
            {CK_PLACES.map(([k, l, sc, x, y]) => (
              <button key={k} className={`lay-pick ${Math.abs(sub.scale - sc) < 0.02 && Math.abs(sub.x - x) < 0.02 ? 'on' : ''}`}
                onClick={() => set({ subject: { scale: sc, x, y } }, 'Posición sobre el fondo')}>{l}</button>
            ))}
          </div>
        </div>
        <label className="insp-field">Tamaño ({Math.round(sub.scale * 100)}%)
          <input type="range" min="0.3" max="1" step="0.05" defaultValue={sub.scale} key={`z${sub.scale}`} onMouseUp={(e) => set({ subject: { scale: +e.target.value } }, 'Tamaño sobre el fondo')} />
        </label>
        <div className="insp-row">
          <label className="insp-field">Color a quitar<input type="color" className="input" value={ck.color} onChange={(e) => set({ color: e.target.value.toUpperCase() })} /></label>
          <button className="btn sm" disabled={busy} onClick={() => detect(false)}>{busy ? 'Midiendo…' : '🎯 Detectar mi verde'}</button>
        </div>
        <label className="insp-field">Tolerancia ({Math.round(ck.similarity * 100)}) — sube si quedan bordes verdes, baja si se borra tu ropa
          <input type="range" min="0.04" max="0.4" step="0.01" defaultValue={ck.similarity} key={`t${ck.similarity}`} onMouseUp={(e) => set({ similarity: +e.target.value }, 'Tolerancia del chroma')} />
        </label>
        <label className="insp-field">Suavizado de bordes ({Math.round(ck.blend * 100)})
          <input type="range" min="0" max="0.3" step="0.01" defaultValue={ck.blend} onMouseUp={(e) => set({ blend: +e.target.value }, 'Suavizado del chroma')} />
        </label>
        <label className="check"><input type="checkbox" checked={ck.despill !== false} onChange={(e) => set({ despill: e.target.checked })} /> Quitar reflejo verde en la piel y el pelo</label>
        {(ck.protect || []).length > 0 && (
          <label className="check" title="Lo verde dentro del recuadro de tu cámara (ropa, objetos) no se vuelve transparente">
            <input type="checkbox" checked onChange={() => set({ protect: [] }, 'Quitar protección de la cámara')} /> Proteger el recuadro de mi cámara
          </label>
        )}
        <p className="muted small">También puedes pedírselo al asistente: «pon croma con el gameplay del emulador de GTA 6 de fondo desde el minuto 2 y ponme abajo a la izquierda».</p>
      </>)}
    </div>
  )
}

/** Tiempo del timeline → tiempo de la toma original (para guardar correcciones que sobreviven a recortes). */
function toSource(tl, t) {
  for (const s of tl.tracks.video) {
    if (t >= s.start - 0.001 && t <= s.start + segLen(s) + 0.001) return { src: s.src_in + (t - s.start), seg: s }
  }
  return null
}

/** Corregir el texto de un subtítulo (o quitarlo). Se guarda en la toma original: sobrevive a re-ediciones. */
function CaptionEditor({ tl, selected, maxWords, onCommit, onSelect }) {
  const start = Number(String(selected).slice(4))
  const groups = useMemo(() => captionGroups(tl.words || [], maxWords), [tl.words, maxWords])
  const g = groups.find((x) => Math.abs(x.start - start) < 0.002) || groups.find((x) => x.start <= start && start < x.end)
  const [text, setText] = useState(g?.text || '')
  useEffect(() => { setText(g?.text || '') }, [selected, g?.text])
  if (!g) return <div className="insp"><div className="insp-title">Subtítulo</div><p className="muted small">Ese subtítulo ya no existe (cambió la edición).</p></div>
  const save = (value) => {
    const a = toSource(tl, g.start), b = toSource(tl, Math.max(g.start, g.end - 0.001))
    if (!a) return
    const srcEnd = b && b.seg === a.seg ? b.src : a.seg.src_out // si cruza un corte, solo dentro de esta toma
    const n = clone(tl)
    const fix = { src_start: +a.src.toFixed(3), src_end: +Math.max(a.src + 0.05, srcEnd).toFixed(3), text: value.trim() }
    n.word_fixes = [...(n.word_fixes || []).filter((f) => f.src_end <= fix.src_start || f.src_start >= fix.src_end), fix]
    // vista inmediata: se reemplazan las palabras de este subtítulo en el timeline
    const words = value.trim().split(/\s+/).filter(Boolean)
    const step = (g.end - g.start) / Math.max(words.length, 1)
    n.words = [...(n.words || []).filter((w) => !(g.start - 0.01 <= (w.start + w.end) / 2 && (w.start + w.end) / 2 <= g.end + 0.01)),
      ...words.map((w, i) => ({ start: +(g.start + i * step).toFixed(3), end: +(g.start + (i + 1) * step - 0.01).toFixed(3), word: w }))]
      .sort((x, y) => x.start - y.start)
    onCommit(n, value.trim() ? 'Corregir subtítulo' : 'Quitar subtítulo')
    if (!value.trim()) onSelect(null)
  }
  return (
    <div className="insp">
      <div className="insp-title">Subtítulo</div>
      <div className="muted small">{g.start.toFixed(2)} s – {g.end.toFixed(2)} s</div>
      <label className="insp-field">Texto
        <textarea className="input" rows={3} value={text} onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); save(text) } }} />
      </label>
      <div className="row">
        <button className="btn btn-primary" disabled={text.trim() === g.text} onClick={() => save(text)}>Guardar corrección</button>
        <button className="btn btn-danger" onClick={() => save('')}><Icon name="trash" /> Quitar este subtítulo</button>
      </div>
      <p className="muted small">Corrige palabras mal escuchadas o nombres (p. ej. «Neva» → «Eneba»). El tiempo se mantiene; la corrección se conserva aunque vuelvas a editar el video. Para el estilo (tamaño, color, posición) usa la pestaña «Estilo».</p>
    </div>
  )
}

export function Inspector({ tl, selected, assetsById, videosById = {}, sourceKind, sourceDuration, playhead, onCommit, onSplit, onSelect, style = {}, onStyle, projectId }) {
  const tlx = useMemo(() => ensureBgIds(clone(tl)), [tl]) // los fondos de la IA sin id también se pueden elegir
  const { track, item } = selected ? findItem(tlx, selected) : { track: null, item: null }
  const [draft, setDraft] = useState(null)
  useEffect(() => { setDraft(item ? clone(item) : null) }, [selected, tl]) // eslint-disable-line

  if (selected && String(selected).startsWith('cap@')) {
    return <CaptionEditor tl={tl} selected={selected} maxWords={style.caption_max_words || 3} onCommit={onCommit} onSelect={onSelect} />
  }
  const lay = tl.layout || { mode: 'crop' }
  const setLayout = (patch, note = 'Encuadre') => { const n = clone(tl); n.layout = { mode: 'crop', game_x: 0.5, ...(n.layout || {}), ...patch }; onCommit(n, note) }
  const layoutBox = sourceKind === 'video' && (
    <div className="insp-box">
      <div className="insp-sub">Encuadre del video</div>
      <div className="lay-picks">
        {LAYOUTS.map(([v, l, h]) => (
          <button key={v} className={`lay-pick ${(lay.mode || 'crop') === v ? 'on' : ''}`} title={h}
            disabled={v === 'split' && !lay.cam} onClick={() => setLayout({ mode: v })}>
            <span className={`lay-ico lay-${v}`} />{l}
          </button>
        ))}
      </div>
      {!lay.cam && <p className="muted small">«Stream» se activa al abrir un clip de stream con tu cámara detectada.</p>}
      {lay.mode === 'split' && (
        <label className="insp-field">Posición del juego ({Math.round((lay.game_x ?? 0.5) * 100)}%)
          <input type="range" min="0" max="1" step="0.01" defaultValue={lay.game_x ?? 0.5}
            onMouseUp={(e) => setLayout({ game_x: +e.target.value }, 'Encuadre del juego')} />
        </label>
      )}
    </div>
  )
  const capBox = onStyle && (
    <div className="insp-box">
      <div className="insp-sub">Subtítulos</div>
      <label className="check"><input type="checkbox" checked={tl.captions?.enabled !== false && style.caption_style !== 'none'}
        onChange={(e) => {
          const n = clone(tl); n.captions = { ...(n.captions || {}), enabled: e.target.checked }; onCommit(n, 'Subtítulos')
          if (e.target.checked && style.caption_style === 'none') onStyle({ caption_style: 'karaoke' })
        }} /> Mostrar subtítulos</label>
      <div className="insp-row">
        <label className="insp-field">Tipo
          <select className="input" value={style.caption_style === 'none' ? 'karaoke' : style.caption_style} onChange={(e) => onStyle({ caption_style: e.target.value })}>
            <option value="karaoke">Palabra resaltada</option>
            <option value="simple">Frases simples</option>
          </select>
        </label>
        <label className="insp-field">Posición
          <select className="input" value={style.caption_position || 'auto'} onChange={(e) => onStyle({ caption_position: e.target.value })}>
            <option value="auto">Automática</option>
            <option value="bottom">Abajo</option>
            <option value="middle">Centro</option>
            <option value="top">Arriba</option>
          </select>
        </label>
      </div>
      <div className="insp-row">
        <label className="insp-field">Tamaño ({style.caption_size})
          <input type="range" min="56" max="140" step="2" defaultValue={style.caption_size} onMouseUp={(e) => onStyle({ caption_size: +e.target.value })} />
        </label>
        <label className="insp-field">Palabras a la vez
          <select className="input" value={style.caption_max_words} onChange={(e) => onStyle({ caption_max_words: +e.target.value })}>
            {[1, 2, 3, 4, 5, 6].map((x) => <option key={x} value={x}>{x}</option>)}
          </select>
        </label>
      </div>
      <div className="insp-row">
        <label className="insp-field">Color<input type="color" className="input" value={style.caption_color || '#ffffff'} onChange={(e) => onStyle({ caption_color: e.target.value.toUpperCase() })} /></label>
        <label className="insp-field">Resalte<input type="color" className="input" value={style.highlight_color || '#ffe500'} onChange={(e) => onStyle({ highlight_color: e.target.value.toUpperCase() })} /></label>
        <label className="check"><input type="checkbox" checked={style.caption_uppercase !== false} onChange={(e) => onStyle({ caption_uppercase: e.target.checked })} /> MAYÚSCULAS</label>
      </div>
      <p className="muted small">Para corregir una palabra, edítala en la transcripción del video.</p>
    </div>
  )

  if (!item || !draft) {
    return (
      <div className="insp">
        <p className="muted small">Selecciona un bloque del timeline para editarlo. Arrastra los bloques para moverlos y sus bordes para recortarlos.</p>
        <div className="insp-actions">
          <button className="btn" onClick={() => onSplit(playhead)}>✂ Dividir la toma en {fmtTime(playhead)}</button>
        </div>
        {layoutBox}
        {sourceKind === 'video' && projectId && <ChromaBox tl={tl} onCommit={onCommit} videosById={videosById} assetsById={assetsById} projectId={projectId} />}
        {capBox}
        <div className="kbd-help muted small">Atajos: <b>Supr</b> borrar · <b>S</b> dividir en el cursor · <b>Ctrl+Z</b> deshacer</div>
      </div>
    )
  }

  const apply = (patch, note = 'Editar elemento') => {
    const n = clone(tlx)
    const it = trackList(n, track).find((x) => x.id === selected)
    Object.assign(it, patch)
    if (track === 'video') {
      it.src_in = Math.max(0, Math.min(it.src_in, it.src_out - 0.3))
      it.src_out = Math.min(sourceDuration, Math.max(it.src_out, it.src_in + 0.3))
      relayout(n)
    } else if ('end' in it) {
      it.start = Math.max(0, Math.min(it.start, n.duration - 0.1))
      it.end = Math.min(n.duration, Math.max(it.end, it.start + 0.1))
    }
    onCommit(n, note)
  }
  const remove = () => { onCommit(deleteItem(clone(tlx), selected), 'Eliminar elemento'); onSelect(null) }
  const set = (k, v) => setDraft({ ...draft, [k]: v })
  const field = (label, k, step = 0.1) => (
    <label className="insp-field">{label}
      <input type="number" step={step} className="input" value={num(draft[k] ?? 0)} onChange={(e) => set(k, +e.target.value)}
        onBlur={() => draft[k] !== item[k] && apply({ [k]: +draft[k] })} />
    </label>
  )
  const title = item?.gen && track === 'broll' ? 'Recurso de la IA' : { video: 'Segmento de la toma', text: 'Texto en pantalla', broll: 'B-roll', music: 'Música', sfx: 'Efecto de sonido', bg: 'Fondo de la IA' }[track]

  return (
    <div className="insp">
      <div className="insp-title">{title}</div>
      {track === 'video' && (<>
        <div className="muted small">En el timeline: {fmtTime(item.start)} – {fmtTime(item.start + segLen(item))} ({num(segLen(item), 1)} s)</div>
        <div className="insp-row">{field('Inicio en la toma (s)', 'src_in')}{field('Fin en la toma (s)', 'src_out')}</div>
        {sourceKind === 'video' && (<>
          <label className="insp-field">Zoom
            <select className="input" value={item.zoom} onChange={(e) => apply({ zoom: +e.target.value }, 'Zoom')}>
              {[1, 1.06, 1.12, 1.18, 1.25, 1.35].map((z) => <option key={z} value={z}>{z === 1 ? 'Sin zoom' : `${z}×`}</option>)}
            </select>
          </label>
          <label className="insp-field">Encuadre horizontal {item.track ? '(siguiendo tu rostro)' : `(${Math.round(item.crop_x * 100)}%)`}
            <input type="range" min="0" max="1" step="0.01" value={draft.crop_x} onChange={(e) => set('crop_x', +e.target.value)}
              onMouseUp={() => apply({ crop_x: draft.crop_x, track: undefined }, 'Encuadre fijo')} />
          </label>
        </>)}
        <label className="insp-field">Transición de entrada
          <select className="input" value={item.transition || 'cut'} onChange={(e) => apply({ transition: e.target.value }, 'Transición')}>
            {TRANS.filter(([v]) => v !== 'slide').map(([v, l]) => <option key={v} value={v}>{l}</option>)}
          </select>
        </label>
        {sourceKind === 'video' && !item.rect && (
          <label className="insp-field">Encuadre de esta toma
            <select className="input" value={item.layout || ''} onChange={(e) => apply({ layout: e.target.value || undefined }, 'Encuadre de la toma')}>
              <option value="">Igual que el video</option>
              {LAYOUTS.filter(([v]) => v !== 'split' || tl.layout?.cam).map(([v, l]) => <option key={v} value={v}>{l}</option>)}
            </select>
          </label>
        )}
        {sourceKind === 'video' && (() => {
          const local = Math.max(0, Math.min(segLen(item) - 0.4, playhead - item.start))
          const fx = item.fx || []
          const setFx = (list, note) => apply({ fx: list }, note)
          return (
            <div className="insp-box">
              <div className="insp-sub">Efectos de la toma ({fx.length})</div>
              {fx.length === 0 && <p className="muted small">Sin zooms ni sacudidas.</p>}
              {fx.map((f, k) => (
                <div key={k} className="fx-row">
                  <span className="fx-kind">{f.type === 'punch' ? '🔍 Zoom' : '〰 Sacudida'}</span>
                  <span className="mono small">{fmtTime(item.start + f.t)}</span>
                  {f.type === 'punch' && (
                    <select className="input sm" title="Cuánto se acerca" value={f.amount} onChange={(e) => setFx(fx.map((x, j) => (j === k ? { ...x, amount: +e.target.value } : x)), 'Zoom')}>
                      {[[0.06, 'suave'], [0.08, 'ligero'], [0.11, 'normal'], [0.15, 'fuerte'], [0.22, 'muy fuerte']].map(([v, l]) => <option key={v} value={v}>{l}</option>)}
                      {![0.06, 0.08, 0.11, 0.15, 0.22].includes(f.amount) && <option value={f.amount}>{Math.round(f.amount * 100)}%</option>}
                    </select>
                  )}
                  {f.type === 'punch' && (
                    <select className="input sm" title="Cuánto se sostiene" value={f.hold ?? 1.1} onChange={(e) => setFx(fx.map((x, j) => (j === k ? { ...x, hold: +e.target.value, hold_fixed: true } : x)), 'Zoom')}>
                      {[0.4, 0.7, 1, 1.5, 2, 3].map((v) => <option key={v} value={v}>{v} s</option>)}
                      {![0.4, 0.7, 1, 1.5, 2, 3].includes(f.hold ?? 1.1) && <option value={f.hold ?? 1.1}>{f.hold ?? 1.1} s</option>}
                    </select>
                  )}
                  <button className="icon-btn sm" title="Quitar efecto" onClick={() => setFx(fx.filter((_, j) => j !== k), 'Quitar efecto')}><Icon name="x" /></button>
                </div>
              ))}
              <div className="insp-actions">
                <button className="btn sm" onClick={() => setFx([...fx, { t: +local.toFixed(2), type: 'punch', amount: 0.11, hold: 1.0, hold_fixed: true }].sort((a, b) => a.t - b.t), 'Zoom')}>+ Zoom en {fmtTime(item.start + local)}</button>
                <button className="btn sm" onClick={() => setFx([...fx, { t: +local.toFixed(2), type: 'shake', amount: 0.16 }].sort((a, b) => a.t - b.t), 'Sacudida')}>+ Sacudida</button>
                {fx.length > 0 && <button className="btn sm" onClick={() => setFx([], 'Quitar efectos')}>Quitar todos</button>}
              </div>
            </div>
          )
        })()}
        <div className="insp-actions">
          <button className="btn" onClick={() => onSplit(playhead)}>✂ Dividir en {fmtTime(playhead)}</button>
          <button className="btn btn-danger" onClick={remove} disabled={tl.tracks.video.length <= 1}><Icon name="trash" /> Quitar segmento</button>
        </div>
      </>)}
      {track === 'text' && (<>
        <textarea className="input full" rows="2" value={draft.text} onChange={(e) => set('text', e.target.value)}
          onBlur={() => draft.text !== item.text && draft.text.trim() && apply({ text: draft.text.trim() }, 'Editar texto')} />
        <label className="insp-field">Tipo
          <select className="input" value={item.kind} onChange={(e) => apply({ kind: e.target.value })}>
            <option value="title">Título (arriba, con fondo)</option>
            <option value="callout">Destacado (centro, grande)</option>
            <option value="lower">Rótulo inferior</option>
            <option value="meme">Meme (grande e inclinado)</option>
          </select>
        </label>
        <div className="insp-row">{field('Inicio (s)', 'start')}{field('Fin (s)', 'end')}</div>
      </>)}
      {(track === 'broll' || track === 'music') && (<>
        <div className="small">{assetsById[item.asset_id]?.title || videosById[item.video_id]?.title || 'Recurso no disponible'}</div>
        <div className="insp-row">{field('Inicio (s)', 'start')}{field('Fin (s)', 'end')}</div>
      </>)}
      {track === 'bg' && (<>
        <div className="small">{item.kind === 'text' ? 'Tarjeta de texto' : (assetsById[item.asset_id]?.title || videosById[item.video_id]?.title || 'Recurso')}</div>
        <div className="insp-row">{field('Inicio (s)', 'start')}{field('Fin (s)', 'end')}</div>
        {item.kind === 'text' ? (
          <label className="insp-field">Texto
            <input className="input" value={draft.text || ''} onChange={(e) => set('text', e.target.value)}
              onBlur={() => draft.text !== item.text && apply({ text: draft.text.toUpperCase() }, 'Texto del fondo')} />
          </label>
        ) : (
          <label className="insp-field">Encuadre
            <select className="input" value={item.fit || 'cover'} onChange={(e) => apply({ fit: e.target.value }, 'Encuadre del fondo')}>
              <option value="cover">Llenar pantalla (recorta)</option>
              <option value="contain">Completo sobre color (logos, capturas)</option>
            </select>
          </label>
        )}
        <p className="muted small">Va detrás de tu cámara (pantalla verde). Arrástralo en «Recursos IA» para moverlo o recortarlo.</p>
      </>)}
      {track === 'broll' && item.mode && item.mode !== 'full' && (<>
        <label className="insp-field">Tipo
          <select className="input" value={item.mode} onChange={(e) => apply({ mode: e.target.value }, 'Tipo de recurso')}>
            <option value="card">Tarjeta con marco</option>
            <option value="pip">Ventana (pip)</option>
            <option value="full">Pantalla completa (tapa la cámara)</option>
          </select>
        </label>
        <label className="insp-field">Posición
          <select className="input" value={JSON.stringify(item.pos || {})} onChange={(e) => apply({ pos: JSON.parse(e.target.value) }, 'Posición del recurso')}>
            <option value={JSON.stringify(item.pos || {})}>Actual</option>
            <option value={JSON.stringify({ x: 0.5, y: 0.17 })}>Arriba</option>
            <option value={JSON.stringify({ x: 0.5, y: 0.72 })}>Abajo</option>
            <option value={JSON.stringify({ x: 0.28, y: 0.42 })}>Izquierda</option>
            <option value={JSON.stringify({ x: 0.72, y: 0.42 })}>Derecha</option>
          </select>
        </label>
        <label className="insp-field">Tamaño ({Math.round((draft.scale ?? 0.4) * 100)}%)
          <input type="range" min="0.15" max="1" step="0.01" value={draft.scale ?? 0.4}
            onChange={(e) => set('scale', +e.target.value)} onMouseUp={() => apply({ scale: draft.scale }, 'Tamaño del recurso')} />
        </label>
        <label className="insp-field">Rótulo
          <input className="input" value={draft.label || ''} maxLength={32} onChange={(e) => set('label', e.target.value)}
            onBlur={() => (draft.label || '') !== (item.label || '') && apply({ label: draft.label }, 'Rótulo')} />
        </label>
      </>)}
      {track === 'broll' && (!item.mode || item.mode === 'full') && (<>
        {item.video_id && field('Desde el segundo del video', 'src_in')}
        <label className="insp-field">Transición de entrada
          <select className="input" value={item.transition || 'cut'} onChange={(e) => apply({ transition: e.target.value }, 'Transición')}>
            {TRANS.filter(([v]) => v !== 'whip').map(([v, l]) => <option key={v} value={v}>{l}</option>)}
          </select>
        </label>
        <label className="insp-field">Encuadre
          <select className="input" value={item.fit || 'cover'} onChange={(e) => apply({ fit: e.target.value }, 'Encuadre b-roll')}>
            <option value="cover">Llenar pantalla (recorta)</option>
            <option value="blur">Completo con fondo desenfocado</option>
          </select>
        </label>
        <label className="insp-field">Audio original de la toma ({Math.round((draft.volume ?? 0) * 100)}%)
          <input type="range" min="0" max="1" step="0.01" value={draft.volume ?? 0}
            onChange={(e) => set('volume', +e.target.value)} onMouseUp={() => apply({ volume: draft.volume }, 'Volumen toma')} />
        </label>
      </>)}
      {track === 'sfx' && (<>
        <div className="small">{assetsById[item.asset_id]?.title}</div>
        {field('Momento (s)', 'start')}
      </>)}
      {(track === 'music' || track === 'sfx') && (
        <label className="insp-field">Volumen ({Math.round((draft.volume ?? 0.5) * 100)}%)
          <input type="range" min="0" max={track === 'sfx' ? 1.5 : 1} step="0.01" value={draft.volume ?? 0.5}
            onChange={(e) => set('volume', +e.target.value)} onMouseUp={() => apply({ volume: draft.volume }, 'Volumen')} />
        </label>
      )}
      {(track === 'text' || (track === 'broll' && item.mode && item.mode !== 'full')) && (<>
        <p className="muted small mt">✥ Arrástralo directamente en la vista previa; la esquina o la rueda del ratón cambian el tamaño{item.pos || item.scale ? '' : ''}.</p>
        {(item.pos || (item.scale && track === 'text')) && <button className="btn sm" onClick={() => apply(track === 'text' ? { pos: undefined, scale: undefined } : { pos: undefined }, 'Posición automática')}>Volver a posición automática</button>}
      </>)}
      {track !== 'video' && <button className="btn btn-danger mt" onClick={remove}><Icon name="trash" /> Eliminar</button>}
    </div>
  )
}

// ---------------- Recursos ----------------

export function AssetsPanel({ tl, playhead, onCommit, notify, onAssetsChanged, settings }) {
  const [kind, setKind] = useState('music')
  const [items, setItems] = useState([])
  const [folder, setFolder] = useState('')
  const [scanKind, setScanKind] = useState('')
  const [playing, setPlaying] = useState(null)
  const audio = useRef(null)

  const load = () => api.assets(kind).then(setItems).catch((e) => notify(e.message, 'error'))
  useEffect(() => { load() }, [kind]) // eslint-disable-line
  useEffect(() => () => audio.current?.pause(), [])

  const toggle = (a) => {
    if (playing === a.id) { audio.current?.pause(); setPlaying(null); return }
    audio.current?.pause()
    if (a.media_type !== 'audio') return
    audio.current = new Audio(a.url)
    audio.current.onended = () => setPlaying(null)
    audio.current.play()
    setPlaying(a.id)
  }

  const add = (a) => {
    const n = clone(tl)
    const t = Math.min(playhead, Math.max(0, n.duration - 0.5))
    if (a.kind === 'music') n.tracks.music.push({ id: uid('m'), asset_id: a.id, start: t, end: n.duration, volume: settings.music_volume })
    if (a.kind === 'sfx') n.tracks.sfx.push({ id: uid('s'), asset_id: a.id, start: t, volume: 0.6 })
    if (a.kind === 'broll') {
      const len = Math.min(a.duration || 3, 3)
      n.tracks.broll.push({ id: uid('b'), asset_id: a.id, start: t, end: Math.min(n.duration, t + len), src_in: 0 })
    }
    onCommit(n, `Añadir ${a.title}`)
  }

  const scan = async () => {
    try {
      const r = await api.scanAssets(folder.trim().replace(/^"|"$/g, ''), scanKind)
      notify(`${r.added} recursos añadidos${r.skipped ? ` (${r.skipped} omitidos)` : ''}`)
      setFolder('')
      load(); onAssetsChanged()
    } catch (e) { notify(e.message, 'error') }
  }

  return (
    <div className="assets-panel">
      <div className="seg seg-full">
        {[['music', 'Música'], ['sfx', 'Efectos'], ['broll', 'B-roll']].map(([k, l]) => (
          <button key={k} className={kind === k ? 'on' : ''} onClick={() => setKind(k)}>{l}</button>
        ))}
      </div>
      <ul className="asset-list">
        {items.length === 0 && <li className="muted small">Sin recursos de este tipo. Agrega una carpeta abajo.</li>}
        {items.map((a) => (
          <li key={a.id} className="asset-item">
            {a.media_type === 'audio'
              ? <button className="icon-btn sm" onClick={() => toggle(a)}>{playing === a.id ? '■' : <Icon name="play" size={12} />}</button>
              : <span className="asset-type">{a.media_type === 'image' ? 'IMG' : 'VID'}</span>}
            <div className="asset-body">
              <div className="asset-title">{a.title}{a.builtin && <span className="badge src-ai">propio</span>}</div>
              <div className="muted small">{a.duration ? `${a.duration.toFixed(1)} s` : ''} {a.tags}</div>
            </div>
            <button className="btn sm" onClick={() => add(a)} title="Añadir en el cursor">+ Añadir</button>
          </li>
        ))}
      </ul>
      <div className="scan-box">
        <label className="field-label">Agregar una carpeta de recursos (dentro de tus carpetas autorizadas)</label>
        <input className="input full" placeholder="C:\Users\TuUsuario\Music\Beats" value={folder} onChange={(e) => setFolder(e.target.value)} />
        <div className="row mt">
          <select className="input" value={scanKind} onChange={(e) => setScanKind(e.target.value)}>
            <option value="">Detectar tipo automáticamente</option>
            <option value="music">Todo es música</option>
            <option value="sfx">Todo son efectos</option>
            <option value="broll">Todo es b-roll</option>
          </select>
          <button className="btn btn-primary" disabled={!folder.trim()} onClick={scan}>Indexar</button>
        </div>
        <p className="muted small">Usa música y clips tuyos o con licencia libre (p. ej. Biblioteca de audio de YouTube, Pixabay).</p>
      </div>
    </div>
  )
}

// ---------------- Estilo ----------------

const STYLE_FIELDS = [
  ['Subtítulos', [
    ['caption_style', 'Estilo', 'select', [['karaoke', 'Palabra resaltada'], ['simple', 'Frases'], ['none', 'Sin subtítulos']]],
    ['caption_position', 'Posición', 'select', [['bottom', 'Abajo'], ['middle', 'Centro'], ['top', 'Arriba']]],
    ['caption_font', 'Fuente', 'text'],
    ['caption_size', 'Tamaño', 'number'],
    ['caption_max_words', 'Palabras por pantalla', 'number'],
    ['caption_color', 'Color', 'color'],
    ['highlight_color', 'Color resaltado', 'color'],
    ['caption_uppercase', 'MAYÚSCULAS', 'bool'],
  ]],
  ['Títulos y destacados', [
    ['title_font', 'Fuente', 'text'],
    ['title_color', 'Texto del título', 'color'],
    ['title_bg', 'Fondo del título', 'color'],
    ['callout_color', 'Color de destacados', 'color'],
  ]],
  ['Ritmo de edición', [
    ['silence_threshold', 'Cortar pausas mayores a (s)', 'number', 0.05],
    ['remove_fillers', 'Quitar muletillas', 'bool'],
    ['zoom_amount', 'Zoom de los cortes', 'number', 0.01],
    ['zoom_every', 'Alternar zoom cada N cortes', 'number'],
    ['sfx_density', 'Whoosh en cortes (0-1)', 'number', 0.05],
    ['callouts_per_min', 'Destacados por minuto (IA)', 'number'],
    ['transition_style', 'Transiciones', 'select', [['dinamico', 'Dinámicas'], ['suave', 'Suaves (fundidos)'], ['cortes', 'Solo cortes']]],
  ]],
  ['Tomas de tus videos (b-roll)', [
    ['broll_fit', 'Encuadre', 'select', [['cover', 'Llenar pantalla'], ['blur', 'Completo + desenfoque']]],
    ['broll_volume', 'Audio original de las tomas', 'number', 0.05],
  ]],
  ['Audio y fondo', [
    ['music_volume', 'Volumen de música', 'number', 0.01],
    ['duck_music', 'Bajar música cuando hablas', 'bool'],
    ['voice_volume', 'Volumen de tu voz', 'number', 0.05],
    ['voice_mode', 'Voz (IA separa voz y música)', 'select', [['original', 'Audio original'], ['realzar', 'Realzar voz sobre el juego'], ['solo_voz', 'Solo voz (quita música de fondo)']]],
    ['pro_audio', 'Audio profesional (-14 LUFS)', 'bool'],
    ['clean_voice', 'Quitar ruido de fondo (solo voz)', 'bool'],
    ['beat_sync', 'Efectos al ritmo de la música', 'bool'],
    ['bg_color_1', 'Fondo (solo voz) 1', 'color'],
    ['bg_color_2', 'Fondo (solo voz) 2', 'color'],
  ]],
]

export function StylePanel({ project, notify, onSaved, onJob }) {
  const [st, setSt] = useState(project.style)
  const [asDefault, setAsDefault] = useState(false)
  const [videos, setVideos] = useState([])
  const [ref, setRef] = useState('')
  const [learned, setLearned] = useState([])
  useEffect(() => { setSt(project.style) }, [project.style])
  useEffect(() => {
    api.library().then((d) => setVideos(d.items)).catch(() => {})
    fetch('/api/studio/styles').then((r) => r.json()).then(setLearned).catch(() => {})
  }, [])
  const applyLearned = async (id) => {
    if (!id) return
    try {
      const r = await fetch(`/api/studio/projects/${project.id}/apply-style`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ style_id: Number(id) }) })
      onSaved(await r.json()); notify('Estilo aprendido aplicado. Pulsa “Auto-editar de nuevo” para aplicar también el ritmo.')
    } catch (e) { notify(e.message, 'error') }
  }

  const save = async () => {
    try { onSaved(await api.studioSaveStyle(project.id, st, asDefault)); notify('Estilo aplicado') } catch (e) { notify(e.message, 'error') }
  }
  const analyze = async () => {
    try { onJob(await api.studioReference(project.id, { video_id: Number(ref) })) } catch (e) { notify(e.message, 'error') }
  }
  const dirty = JSON.stringify(st) !== JSON.stringify(project.style)

  return (
    <div className="style-panel">
      {learned.length > 0 && (
        <div className="ref-box">
          <label className="field-label">Aplicar un estilo aprendido</label>
          <select className="input full" defaultValue="" onChange={(e) => applyLearned(e.target.value)}>
            <option value="">Elige un estilo…</option>
            {learned.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
          </select>
        </div>
      )}
      <div className="ref-box">
        <label className="field-label">Copiar el ritmo de un video de referencia</label>
        <div className="row">
          <select className="input" value={ref} onChange={(e) => setRef(e.target.value)}>
            <option value="">Elige un video de tu biblioteca…</option>
            {videos.map((v) => <option key={v.id} value={v.id}>{v.title}</option>)}
          </select>
          <button className="btn" disabled={!ref} onClick={analyze}>Analizar</button>
        </div>
        <p className="muted small">Mide cortes por minuto y velocidad del habla, y ajusta el ritmo de edición. Luego pulsa “Auto-editar de nuevo”.</p>
      </div>
      {STYLE_FIELDS.map(([group, fields]) => (
        <fieldset key={group} className="style-group">
          <legend>{group}</legend>
          {fields.map(([k, label, type, extra]) => (
            <label key={k} className={`style-field ${type === 'bool' ? 'is-bool' : ''}`}>
              <span>{label}</span>
              {type === 'select' && <select className="input" value={st[k]} onChange={(e) => setSt({ ...st, [k]: e.target.value })}>
                {extra.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select>}
              {type === 'text' && <input className="input" value={st[k]} onChange={(e) => setSt({ ...st, [k]: e.target.value })} />}
              {type === 'number' && <input className="input" type="number" step={extra || 1} value={st[k]} onChange={(e) => setSt({ ...st, [k]: +e.target.value })} />}
              {type === 'color' && <input type="color" value={st[k]} onChange={(e) => setSt({ ...st, [k]: e.target.value.toUpperCase() })} />}
              {type === 'bool' && <input type="checkbox" checked={!!st[k]} onChange={(e) => setSt({ ...st, [k]: e.target.checked })} />}
            </label>
          ))}
        </fieldset>
      ))}
      <label className="check"><input type="checkbox" checked={asDefault} onChange={(e) => setAsDefault(e.target.checked)} /> Usar como mi estilo para proyectos nuevos</label>
      <button className="btn btn-primary full" onClick={save} disabled={!dirty && !asDefault}>Aplicar estilo</button>
    </div>
  )
}

// ---------------- IA ----------------

const EXAMPLES = [
  'Pon los subtítulos en amarillo y en el centro',
  'Agrega un texto destacado cuando digo la cifra más importante',
  'Quita la música de los primeros 5 segundos',
  'Más zoom en la parte final',
  'Agrega un whoosh en cada cambio de tema',
  'Cambia el título por algo más llamativo',
  'Pon un gameplay de GTA 6 de fondo en todo el video',
  'Busca gameplay de Minecraft y ponlo detrás de mi cámara',
  'Pon este video de la biblioteca como fondo de pantalla verde',
  'Quita el fondo y pon un color negro detrás',
]

export function AiBox({ project, onJob, notify, log, llmReady }) {
  const [text, setText] = useState('')
  const [model, setModel] = useModelChoice()
  const send = async (t) => {
    const msg = (t ?? text).trim()
    if (!msg) return
    try { onJob(await api.studioInstruct(project.id, msg, model), msg); setText('') } catch (e) { notify(e.message, 'error') }
  }
  return (
    <div className="ai-box">
      {!llmReady && <div className="alert alert-warn">Abre Ollama para dar instrucciones a la IA.</div>}
      <label className="field-label">Dile a tu IA qué cambiar</label>
      <textarea className="input full" rows="3" value={text} placeholder="Ej. pon un texto 'ERROR #1' cuando hablo del primer error y un impacto"
        onChange={(e) => setText(e.target.value)} onKeyDown={(e) => { if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) send() }} />
      <ModelPicker value={model} onChange={setModel} label="Modelo para esta instrucción" />
      <button className="btn btn-primary full mt" disabled={!text.trim() || !llmReady} onClick={() => send()}>Enviar instrucción (Ctrl+Enter)</button>
      <div className="chips">{EXAMPLES.map((e) => <button key={e} className="chip" onClick={() => setText(e)}>{e}</button>)}</div>
      {log.length > 0 && (
        <ul className="ai-log">
          {log.map((l, i) => (
            <li key={i}>
              <div className="ai-q">“{l.q}”</div>
              <div className={`ai-a ${l.error ? 'bad' : ''}`}>{l.a || 'Procesando…'}</div>
              {l.skipped?.length > 0 && <div className="muted small">Omitido: {l.skipped.join('; ')}</div>}
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
