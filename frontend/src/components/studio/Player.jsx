import { forwardRef, useCallback, useEffect, useImperativeHandle, useRef, useState } from 'react'
import Icon from '../Icon.jsx'

const SPEEDS = [0.25, 0.5, 0.75, 1, 1.25, 1.5, 2]

export function tc(t, fps = 30, frames = true) {
  if (!Number.isFinite(t) || t < 0) t = 0
  const h = Math.floor(t / 3600), m = Math.floor((t % 3600) / 60), s = Math.floor(t % 60)
  const f = Math.floor((t % 1) * fps)
  const ss = String(s).padStart(2, '0')
  const base = h ? `${h}:${String(m).padStart(2, '0')}:${ss}` : `${m}:${ss}`
  return frames ? `${base}.${String(f).padStart(2, '0')}` : base
}

const store = {
  get: (k, d) => { try { const v = localStorage.getItem(k); return v === null ? d : JSON.parse(v) } catch { return d } },
  set: (k, v) => { try { localStorage.setItem(k, JSON.stringify(v)) } catch { /* sin almacenamiento */ } },
}

/**
 * Reproductor estilo YouTube: controles sobre el video, volumen desplegable, ajustes (velocidad, bucle),
 * modo cine, pantalla completa y marcas de corte / capítulo en la barra. Se adapta al espacio del contenedor.
 */
const Player = forwardRef(function Player({ src, engine, fps = 30, cuts = [], chapters = [], badge, empty, onTime, theater, onTheater, overlay }, ref) {
  const wrap = useRef(null)
  const v = useRef(null)
  const stage = useRef(null)
  const [liveMsg, setLiveMsg] = useState(null)
  const has = !!(src || engine)
  const bar = useRef(null)
  const [playing, setPlaying] = useState(false)
  const [t, setT] = useState(0)
  const [dur, setDur] = useState(0)
  const [buffered, setBuffered] = useState(0)
  const [vol, setVol] = useState(() => store.get('smc_vol', 1))
  const [muted, setMuted] = useState(() => store.get('smc_muted', false))
  const [rate, setRate] = useState(1)
  const [loop, setLoop] = useState(false)
  const [full, setFull] = useState(false)
  const [hover, setHover] = useState(null)
  const [scrub, setScrub] = useState(false)
  const [menu, setMenu] = useState(false)
  const [idle, setIdle] = useState(false)
  const [flash, setFlash] = useState(null) // icono central breve (play/pausa/±5 s/volumen)
  const idleTimer = useRef(null)
  const flashTimer = useRef(null)

  const showFlash = (k) => { setFlash(k); clearTimeout(flashTimer.current); flashTimer.current = setTimeout(() => setFlash(null), 550) }

  const seek = useCallback((x) => {
    const el = v.current
    if (!el) return
    const d = el.duration || 0
    el.currentTime = Math.max(0, Math.min(d || x, x))
    setT(el.currentTime)
    onTime?.(el.currentTime)
  }, [onTime])

  const toggle = useCallback(() => {
    const el = v.current
    if (!el) return
    if (el.paused) { el.play().catch(() => {}); showFlash('play') } else { el.pause(); showFlash('pause') }
  }, [])

  const step = useCallback((frames) => { v.current?.pause(); seek((v.current?.currentTime || 0) + frames / fps) }, [seek, fps])
  const jumpCut = useCallback((dir) => {
    const now = v.current?.currentTime || 0
    const pts = [0, ...cuts, dur].sort((a, b) => a - b)
    const target = dir > 0 ? pts.find((p) => p > now + 0.05) : [...pts].reverse().find((p) => p < now - 0.3)
    if (target !== undefined) seek(target)
  }, [cuts, dur, seek])
  const fullscreen = useCallback(() => {
    if (document.fullscreenElement) document.exitFullscreen()
    else wrap.current?.requestFullscreen?.()
  }, [])
  const changeVol = useCallback((d) => {
    setMuted(false)
    setVol((x) => Math.max(0, Math.min(1, Math.round((x + d) * 20) / 20)))
    showFlash('vol')
  }, [])

  useImperativeHandle(ref, () => ({
    seek, toggle, step, jumpCut, fullscreen, changeVol,
    get paused() { return v.current?.paused ?? true },
    get currentTime() { return v.current?.currentTime || 0 },
    play: () => v.current?.play().catch(() => {}),
    pause: () => v.current?.pause(),
    mute: () => { setMuted((m) => !m); showFlash('vol') },
  }), [seek, toggle, step, jumpCut, fullscreen, changeVol])

  // Vista en vivo: el motor se comporta como un <video>
  useEffect(() => {
    if (!engine || !stage.current) return
    v.current = engine
    engine.mount(stage.current)
    engine.volume = vol; engine.muted = muted; engine.playbackRate = rate
    const on = {
      play: () => setPlaying(true), pause: () => setPlaying(false),
      loadedmetadata: () => setDur(engine.duration), durationchange: () => setDur(engine.duration),
      timeupdate: () => { if (engine.paused) { setT(engine.currentTime); onTime?.(engine.currentTime) } },
      liveerror: () => setLiveMsg(engine.error), livestatus: () => setLiveMsg(engine.status),
    }
    Object.entries(on).forEach(([k, f]) => engine.addEventListener(k, f))
    setDur(engine.duration)
    const ro = new ResizeObserver(() => engine.draw())
    ro.observe(stage.current)
    return () => { Object.entries(on).forEach(([k, f]) => engine.removeEventListener(k, f)); ro.disconnect(); engine.pause() }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [engine])

  useEffect(() => {
    const onFs = () => setFull(document.fullscreenElement === wrap.current)
    document.addEventListener('fullscreenchange', onFs)
    return () => document.removeEventListener('fullscreenchange', onFs)
  }, [])
  useEffect(() => {
    if (v.current) { v.current.volume = vol; v.current.muted = muted }
    store.set('smc_vol', vol); store.set('smc_muted', muted)
  }, [vol, muted, src, engine])
  useEffect(() => { if (v.current) v.current.playbackRate = rate }, [rate, src, engine])

  // Cabezal fluido mientras se reproduce
  useEffect(() => {
    if (!playing) return
    let raf
    const tick = () => { const x = v.current?.currentTime || 0; setT(x); onTime?.(x); raf = requestAnimationFrame(tick) }
    raf = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(raf)
  }, [playing, onTime])

  const posToTime = (clientX) => {
    const r = bar.current.getBoundingClientRect()
    return Math.max(0, Math.min(1, (clientX - r.left) / r.width)) * (dur || 0)
  }
  useEffect(() => {
    if (!scrub) return
    const move = (e) => seek(posToTime(e.clientX))
    const up = () => setScrub(false)
    window.addEventListener('mousemove', move)
    window.addEventListener('mouseup', up)
    return () => { window.removeEventListener('mousemove', move); window.removeEventListener('mouseup', up) }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [scrub])

  const wake = () => {
    setIdle(false)
    clearTimeout(idleTimer.current)
    idleTimer.current = setTimeout(() => setIdle(true), 2600)
  }

  useEffect(() => { if (v.current && engine) v.current.loop = loop }, [loop, engine])
  const pct = dur ? (t / dur) * 100 : 0
  const chapterAt = (x) => [...chapters].reverse().find((c) => c.start <= x)?.text
  const hideUi = playing && idle && !menu && !scrub
  const volIcon = muted || vol === 0 ? 'mute' : 'volume'

  return (
    <div ref={wrap} className={`player ${full ? 'is-full' : ''} ${hideUi ? 'ui-hidden' : ''}`}
      onMouseMove={wake} onMouseLeave={() => { if (playing) setIdle(true) }}>
      {engine
        ? <div ref={stage} className="live-stage" onClick={() => { setMenu(false); toggle() }} onDoubleClick={fullscreen}
            onWheel={(e) => { if (e.shiftKey || e.altKey) { e.preventDefault(); changeVol(e.deltaY < 0 ? 0.05 : -0.05) } }} />
        : src
        ? <video ref={v} src={src} loop={loop} playsInline preload="auto" onClick={() => { setMenu(false); toggle() }} onDoubleClick={fullscreen}
            onPlay={() => setPlaying(true)} onPause={() => setPlaying(false)}
            onLoadedMetadata={(e) => { setDur(e.target.duration); e.target.volume = vol; e.target.muted = muted; e.target.playbackRate = rate }}
            onProgress={(e) => { const b = e.target.buffered; if (b.length) setBuffered(b.end(b.length - 1)) }}
            onWheel={(e) => { if (e.shiftKey || e.altKey) { e.preventDefault(); changeVol(e.deltaY < 0 ? 0.05 : -0.05) } }}
            onTimeUpdate={(e) => { if (!playing) { setT(e.target.currentTime); onTime?.(e.target.currentTime) } }} />
        : <div className="pv-empty muted">{empty}</div>}

      {overlay}
      {liveMsg && <div className="pv-stale">{liveMsg}</div>}
      {badge && <div className="pv-stale">{badge}</div>}
      {has && !playing && !scrub && <button className="player-bigplay" onClick={toggle} aria-label="Reproducir"><Icon name="play" size={30} /></button>}
      {flash && (
        <div className="player-flash" key={flash + t}>
          {flash === 'vol' ? <><Icon name={volIcon} size={22} /><span>{muted ? 'Silencio' : `${Math.round(vol * 100)}%`}</span></>
            : <Icon name={flash} size={26} />}
        </div>
      )}

      {has && (
        <div className="player-ui" onClick={(e) => e.stopPropagation()}>
          <div className="player-bar" ref={bar}
            onMouseDown={(e) => { setScrub(true); seek(posToTime(e.clientX)) }}
            onMouseMove={(e) => setHover(posToTime(e.clientX))} onMouseLeave={() => setHover(null)}>
            <div className="pb-track">
              <div className="pb-buf" style={{ width: `${dur ? (buffered / dur) * 100 : 0}%` }} />
              {hover !== null && <div className="pb-hover" style={{ width: `${dur ? (hover / dur) * 100 : 0}%` }} />}
              <div className="pb-fill" style={{ width: `${pct}%` }} />
              {dur > 0 && cuts.map((c, i) => <span key={i} className="pb-cut" style={{ left: `${(c / dur) * 100}%` }} />)}
              {dur > 0 && chapters.map((c, i) => <span key={`c${i}`} className="pb-chapter" style={{ left: `${(c.start / dur) * 100}%` }} />)}
              <span className="pb-knob" style={{ left: `${pct}%` }} />
            </div>
            {hover !== null && dur > 0 && (
              <div className="pb-tip" style={{ left: `clamp(40px, ${(hover / dur) * 100}%, calc(100% - 40px))` }}>
                {chapterAt(hover) && <b>{chapterAt(hover)}</b>}{tc(hover, fps, false)}
              </div>
            )}
          </div>

          <div className="player-row">
            <button className="pc-btn" title={playing ? 'Pausa (K / Espacio)' : 'Reproducir (K / Espacio)'} onClick={toggle}>
              <Icon name={playing ? 'pause' : 'play'} size={22} />
            </button>
            <button className="pc-btn" title="Toma anterior (↑)" onClick={() => jumpCut(-1)}><Icon name="prev" size={18} /></button>
            <button className="pc-btn" title="Toma siguiente (↓)" onClick={() => jumpCut(1)}><Icon name="next" size={18} /></button>
            <div className="pc-volume">
              <button className="pc-btn" title="Silenciar (M) · Volumen: Shift + rueda" onClick={() => setMuted(!muted)}>
                <Icon name={volIcon} size={20} />
              </button>
              <input className="pc-vol" type="range" min="0" max="1" step="0.01" value={muted ? 0 : vol}
                style={{ '--v': `${(muted ? 0 : vol) * 100}%` }}
                onChange={(e) => { setVol(+e.target.value); setMuted(+e.target.value === 0) }} aria-label="Volumen" />
            </div>
            <span className="pc-time">{tc(t, fps, false)} / {tc(dur, fps, false)}</span>
            {chapterAt(t) && <span className="pc-chapter">· {chapterAt(t)}</span>}
            <span className="pc-spacer" />
            <span className="pc-frames" title="Tiempo exacto (fotogramas)">{tc(t, fps)}</span>
            <button className="pc-btn sm" title="Fotograma anterior ( , )" onClick={() => step(-1)}>‹</button>
            <button className="pc-btn sm" title="Fotograma siguiente ( . )" onClick={() => step(1)}>›</button>
            <button className={`pc-btn ${loop ? 'on' : ''}`} title="Repetir" onClick={() => setLoop(!loop)}><Icon name="loop" size={18} /></button>
            <div className="pc-menu-wrap">
              <button className={`pc-btn ${menu ? 'on' : ''}`} title="Ajustes" onClick={() => setMenu(!menu)}>
                <Icon name="gear" size={19} />{rate !== 1 && <span className="pc-badge">{rate}×</span>}
              </button>
              {menu && (
                <div className="pc-menu">
                  <div className="pc-menu-title">Velocidad</div>
                  <div className="pc-speeds">
                    {SPEEDS.map((s) => <button key={s} className={s === rate ? 'on' : ''} onClick={() => setRate(s)}>{s === 1 ? 'Normal' : `${s}×`}</button>)}
                  </div>
                  <label className="pc-menu-row"><input type="checkbox" checked={loop} onChange={(e) => setLoop(e.target.checked)} /> Repetir</label>
                  <div className="pc-menu-title">Atajos</div>
                  <div className="pc-keys">
                    <span>Espacio / K</span><span>Reproducir / pausa</span>
                    <span>J / L</span><span>−5 s / +5 s</span>
                    <span>← / →</span><span>±1 s (Shift ±5 s)</span>
                    <span>, / .</span><span>Fotograma</span>
                    <span>↑ / ↓</span><span>Toma anterior / siguiente</span>
                    <span>M · Shift+rueda</span><span>Silenciar · volumen</span>
                    <span>T · F</span><span>Modo cine · pantalla completa</span>
                  </div>
                </div>
              )}
            </div>
            {onTheater && !full && (
              <button className={`pc-btn ${theater ? 'on' : ''}`} title={theater ? 'Vista normal (T)' : 'Modo cine: video más grande (T)'} onClick={onTheater}>
                <Icon name="theater" size={20} />
              </button>
            )}
            <button className="pc-btn" title={full ? 'Salir de pantalla completa (F / Esc)' : 'Pantalla completa (F)'} onClick={fullscreen}>
              <Icon name={full ? 'shrink' : 'expand'} size={20} />
            </button>
          </div>
        </div>
      )}
    </div>
  )
})

export default Player
