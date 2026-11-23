import { useEffect, useMemo, useRef, useState } from 'react'
import Icon from '../Icon.jsx'
import { captionGroups, clone, ensureBgIds, relayout, rippleShift, segLen, trackList } from './tl.js'

const ROWS = [
  { key: 'text', label: 'Textos' },
  { key: 'ai', label: 'Recursos IA', title: 'Imágenes, logos, capturas y clips que la IA encontró en internet (tarjetas y fondos)' },
  { key: 'broll', label: 'B-roll' },
  { key: 'video', label: 'Toma' },
  { key: 'captions', label: 'Subtítulos' },
  { key: 'music', label: 'Música' },
  { key: 'sfx', label: 'Efectos' },
]

function fmt(t) {
  const m = Math.floor(t / 60)
  return `${m}:${String(Math.floor(t % 60)).padStart(2, '0')}`
}

/**
 * Timeline multipista. Arrastra bloques para moverlos, sus bordes para recortarlos.
 * En la pista "Toma", recortar los bordes quita o recupera material (ripple).
 */
export default function Timeline({ height, tl, assetsById, videosById = {}, sourceDuration, playhead, onSeek, selected, onSelect, onCommit, maxWords, onSplit, onDelete }) {
  const scroller = useRef(null)
  const [pps, setPps] = useState(40)
  const [drag, setDrag] = useState(null) // { id, track, mode, x0, orig, base }
  const [preview, setPreview] = useState(null) // timeline temporal mientras se arrastra
  const previewRef = useRef(null)
  const base = useMemo(() => ensureBgIds(clone(tl)), [tl]) // copia con ids en los fondos de la IA (no en cada cuadro)
  const view = preview || base
  const dur = Math.max(view.duration, 1)
  const aiBroll = view.tracks.broll.filter((b) => b.gen)
  const userBroll = view.tracks.broll.filter((b) => !b.gen)
  const bgSched = trackList(view, 'bg')

  const fitPps = () => {
    const w = scroller.current?.clientWidth || 900
    return Math.max(0.5, (w - 130) / Math.max(tl.duration, 1))
  }
  const minPps = Math.min(6, fitPps())
  // Ajustar zoom para que quepa al abrir
  useEffect(() => {
    setPps(Math.max(minPps, Math.min(120, fitPps())))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tl.width])

  // Zoom con Ctrl + rueda, anclado al ratón
  useEffect(() => {
    const el = scroller.current
    if (!el) return
    const onWheel = (e) => {
      if (!e.ctrlKey && !e.metaKey) return
      e.preventDefault()
      const rect = el.getBoundingClientRect()
      const x = e.clientX - rect.left - 110 + el.scrollLeft
      setPps((p) => {
        const np = Math.max(minPps, Math.min(300, p * (e.deltaY < 0 ? 1.2 : 1 / 1.2)))
        requestAnimationFrame(() => { el.scrollLeft = (x / p) * np - (e.clientX - rect.left - 110) })
        return np
      })
    }
    el.addEventListener('wheel', onWheel, { passive: false })
    return () => el.removeEventListener('wheel', onWheel)
  }, [minPps])

  // El timeline sigue al cabezal cuando sale de la vista
  useEffect(() => {
    const el = scroller.current
    if (!el || drag) return
    const x = 110 + playhead * pps
    if (x < el.scrollLeft + 110 || x > el.scrollLeft + el.clientWidth - 40) el.scrollLeft = Math.max(0, x - 110 - el.clientWidth * 0.15)
  }, [playhead, pps, drag])

  const zoomBy = (f) => setPps((p) => Math.max(minPps, Math.min(300, p * f)))

  const caps = useMemo(() => captionGroups(view.words || [], maxWords), [view.words, maxWords])

  const startDrag = (e, track, item, mode) => {
    e.stopPropagation()
    e.preventDefault()
    onSelect(item.id)
    setDrag({ id: item.id, track, mode, x0: e.clientX, orig: clone(item), base: ensureBgIds(clone(tl)) })
  }

  useEffect(() => {
    if (!drag) return
    const move = (e) => {
      const d = (e.clientX - drag.x0) / pps
      const next = clone(drag.base)
      const it = trackList(next, drag.track).find((x) => x.id === drag.id)
      const o = drag.orig
      if (drag.track === 'video') {
        if (drag.mode === 'l') {
          const nin = Math.min(Math.max(0, o.src_in + d), o.src_out - 0.3)
          const delta = o.src_in - nin // + recupera material
          it.src_in = nin
          rippleShift(next, o.start + 0.001, delta)
        } else if (drag.mode === 'r') {
          const nout = Math.max(Math.min(sourceDuration, o.src_out + d), o.src_in + 0.3)
          const delta = nout - o.src_out
          it.src_out = nout
          rippleShift(next, o.start + segLen(o), delta)
        }
        relayout(next)
      } else {
        const len = (o.end ?? o.start) - o.start
        if (drag.mode === 'move') {
          it.start = Math.min(Math.max(0, o.start + d), Math.max(0, next.duration - Math.max(len, 0.1)))
          if ('end' in it) it.end = it.start + len
        } else if (drag.mode === 'l') {
          it.start = Math.min(Math.max(0, o.start + d), o.end - 0.2)
          if (drag.track === 'broll') it.src_in = Math.max(0, (o.src_in || 0) + (it.start - o.start))
        } else if (drag.mode === 'r') {
          it.end = Math.min(Math.max(o.start + 0.2, o.end + d), next.duration)
        }
      }
      previewRef.current = next
      setPreview(next)
    }
    const up = () => {
      const p = previewRef.current
      previewRef.current = null
      setDrag(null)
      setPreview(null)
      if (p && JSON.stringify(p) !== JSON.stringify(drag.base)) onCommit(p, drag.track === 'video' ? 'Recortar toma' : 'Mover/recortar elemento')
    }
    window.addEventListener('mousemove', move)
    window.addEventListener('mouseup', up)
    return () => { window.removeEventListener('mousemove', move); window.removeEventListener('mouseup', up) }
  }, [drag, pps, sourceDuration, onCommit])

  const seekFromEvent = (e) => {
    const rect = e.currentTarget.getBoundingClientRect()
    onSeek(Math.max(0, Math.min(dur, (e.clientX - rect.left) / pps)))
  }

  const width = dur * pps
  const step = pps > 60 ? 1 : pps > 25 ? 2 : pps > 12 ? 5 : pps > 5 ? 10 : pps > 2.5 ? 30 : pps > 1 ? 60 : 120
  const ticks = []
  for (let t = 0; t <= dur; t += step) ticks.push(t)

  const block = (track, it, cls, label, resizable = true, thumb = null) => {
    const len = track === 'video' ? segLen(it) : track === 'sfx' ? 0.4 : it.end - it.start
    return (
      <div key={it.id} className={`tl-block ${cls} ${selected === it.id ? 'sel' : ''}`}
        style={{ left: it.start * pps, width: Math.max(len * pps, 6), ...(thumb ? { '--thumb': `url(${thumb})` } : {}) }}
        onMouseDown={(e) => (track === 'video' ? (e.stopPropagation(), onSelect(it.id)) : startDrag(e, track, it, 'move'))}
        title={label}>
        {resizable && <span className="tl-h l" onMouseDown={(e) => startDrag(e, track, it, 'l')} />}
        <span className="tl-label">{label}</span>
        {resizable && <span className="tl-h r" onMouseDown={(e) => startDrag(e, track, it, 'r')} />}
      </div>
    )
  }

  return (
    <div className="timeline-wrap" style={height ? { height } : undefined}>
      <div className="tl-toolbar">
        <span className="tl-time">{fmt(playhead)} <span className="muted">/ {fmt(view.duration)}</span></span>
        <span className="tl-sep" />
        <button className="tl-tool" title="Dividir en el cabezal (S)" onClick={() => onSplit?.(playhead)}><Icon name="split" size={15} /> Dividir</button>
        <button className="tl-tool" title="Eliminar selección (Supr)" disabled={!selected} onClick={() => onDelete?.()}><Icon name="trash" size={15} /> Eliminar</button>
        <span className="tl-sep" />
        <span className="muted small">{view.tracks.video.length} tomas · {view.tracks.text.length} textos · {aiBroll.length + bgSched.length} recursos IA · {view.tracks.sfx.length} efectos</span>
        <span className="pc-spacer" />
        <button className="tl-tool" title="Alejar" onClick={() => zoomBy(1 / 1.4)}><Icon name="zoomout" size={15} /></button>
        <input type="range" min={Math.log(minPps)} max={Math.log(300)} step="0.01" value={Math.log(pps)}
          onChange={(e) => setPps(Math.exp(+e.target.value))} title="Zoom (Ctrl + rueda)" />
        <button className="tl-tool" title="Acercar" onClick={() => zoomBy(1.4)}><Icon name="zoomin" size={15} /></button>
        <button className="tl-tool" title="Ver todo el video" onClick={() => { setPps(fitPps()); scroller.current.scrollLeft = 0 }}><Icon name="fit" size={15} /> Ajustar</button>
      </div>
      <div className="tl-scroller" ref={scroller}>
        <div className="tl-inner" style={{ width: width + 110 }}>
          <div className="tl-row tl-ruler">
            <div className="tl-head" />
            <div className="tl-lane tl-scrub" style={{ width }} onMouseDown={(e) => {
              const lane = e.currentTarget
              const at = (ev) => { const r = lane.getBoundingClientRect(); onSeek(Math.max(0, Math.min(dur, (ev.clientX - r.left) / pps))) }
              at(e)
              const up = () => { window.removeEventListener('mousemove', at); window.removeEventListener('mouseup', up) }
              window.addEventListener('mousemove', at); window.addEventListener('mouseup', up)
            }}>
              {ticks.map((t) => <span key={t} className="tl-tick" style={{ left: t * pps }}>{fmt(t)}</span>)}
            </div>
          </div>
          {ROWS.map((r) => (
            <div key={r.key} className={`tl-row row-${r.key}`}>
              <div className="tl-head" title={r.title}>{r.label}</div>
              <div className="tl-lane" style={{ width }} onMouseDown={(e) => { onSelect(null); seekFromEvent(e) }}>
                {r.key === 'video' && view.tracks.video.map((s, i) =>
                  block('video', s, `seg ${s.zoom > 1 ? 'zoomed' : ''}`, `${i + 1}${s.zoom > 1 ? ` · zoom ${s.zoom}×` : ''}`))}
                {r.key === 'text' && view.tracks.text.map((t) => block('text', t, `txt k-${t.kind}`, t.text))}
                {r.key === 'broll' && userBroll.map((b) => block('broll', b, `broll tr-${b.transition || 'cut'}`, assetsById[b.asset_id]?.title || videosById[b.video_id]?.title || 'Toma'))}
                {r.key === 'ai' && bgSched.map((e) => block('bg', e, `ai bg ${e.kind === 'text' ? 'k-text' : ''}`,
                  e.kind === 'text' ? `🔤 ${e.text}` : `🖼 Fondo · ${assetsById[e.asset_id]?.title || videosById[e.video_id]?.title || 'recurso'}`,
                  true, e.asset_id && assetsById[e.asset_id]?.media_type === 'image' ? `/api/studio/assets/${e.asset_id}/file` : null))}
                {r.key === 'ai' && aiBroll.map((b) => block('broll', b, `ai card m-${b.mode || 'full'}`,
                  `${b.mode === 'card' ? '🃏' : b.mode === 'pip' ? '▣' : '🎞'} ${assetsById[b.asset_id]?.title || videosById[b.video_id]?.title || 'recurso'}${b.label ? ` · ${b.label}` : ''}`,
                  true, b.asset_id && assetsById[b.asset_id]?.media_type === 'image' ? `/api/studio/assets/${b.asset_id}/file` : null))}
                {r.key === 'ai' && !aiBroll.length && !bgSched.length && <span className="muted small tl-off">Sin recursos de la IA (actívalos con «Recursos de internet»)</span>}
                {r.key === 'music' && view.tracks.music.map((m) => block('music', m, 'music', `${assetsById[m.asset_id]?.title || 'Música'} · ${Math.round((m.volume ?? 0.14) * 100)}%`))}
                {r.key === 'sfx' && view.tracks.sfx.map((s) => block('sfx', s, 'sfx', assetsById[s.asset_id]?.title || 'SFX', false))}
                {r.key === 'captions' && (view.captions?.enabled
                  ? caps.map((c, i) => {
                    const cid = `cap@${c.start.toFixed(3)}`
                    return <div key={i} className={`tl-cap ${selected === cid ? 'sel' : ''}`} style={{ left: c.start * pps, width: Math.max((c.end - c.start) * pps, 3) }}
                      title={`${c.text} — clic para corregir este subtítulo`}
                      onMouseDown={(e) => { e.stopPropagation(); onSelect(cid); onSeek(c.start + 0.01) }}>{pps > 30 ? c.text : ''}</div>
                  })
                  : <span className="muted small tl-off">Subtítulos desactivados</span>)}
              </div>
            </div>
          ))}
          <div className="tl-playhead" style={{ left: 110 + playhead * pps }} />
        </div>
      </div>
    </div>
  )
}
