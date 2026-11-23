// Vista previa EN VIVO del timeline (sin renderizar): reproduce tu toma original y dibuja en un lienzo el
// encuadre, los zooms, las sacudidas, el b-roll, los textos y los subtítulos con la misma geometría que el
// render final (studio/render.py). Se comporta como un <video> para el reproductor (play, pause, currentTime…).

const PUNCH_IN = 0.12, PUNCH_OUT = 0.28
const CAM_FRAC = 680 / 1920
const clamp = (x, a, b) => Math.max(a, Math.min(b, x))
const even = (x) => Math.max(2, Math.floor(Math.round(x) / 2) * 2)

export const segLen = (s) => s.src_out - s.src_in

// ---------- tarjetas de b-roll y fondo por tramos (idéntico a timeline.broll_opts / render.card_geometry) ----------
const num = (v, lo, hi, d) => { const x = Number(v); return Number.isFinite(x) ? clamp(x, lo, hi) : d }
export function brollOpts(b, isImage = false) {
  const mode = ['full', 'card', 'pip'].includes(b.mode) ? b.mode : 'full'
  const pos = b.pos && typeof b.pos === 'object' ? b.pos : {}
  const [dx, dy] = mode === 'pip' ? [0.8, 0.26] : [0.68, 0.45]
  return {
    mode,
    pos: { x: num(pos.x, 0, 1, dx), y: num(pos.y, 0, 1, dy) },
    scale: num(b.scale, 0.1, 1, mode === 'pip' ? 0.3 : 0.42),
    rot: num(b.rot, -12, 12, 0),
    anim: ['pop', 'slide_left', 'slide_right', 'slide_up', 'fade', 'none'].includes(b.anim) ? b.anim : (mode !== 'full' ? 'pop' : 'none'),
    frame: ['white', 'shadow', 'none'].includes(b.frame) ? b.frame : (mode === 'card' ? 'white' : 'shadow'),
    kenburns: 'kenburns' in b ? !!b.kenburns : (isImage && 'mode' in b),
    label: String(b.label || '').trim().slice(0, 80),
  }
}
export function cardGeometry(mw, mh, o, OW, OH) {
  const W = o.scale * OW
  const b = o.frame === 'white' ? Math.round(0.022 * W) : 0
  let iw = W - 2 * b, ih = iw * mh / Math.max(mw, 1)
  const maxh = 0.6 * OH - 2 * b
  if (ih > maxh) { ih = maxh; iw = ih * mw / Math.max(mh, 1) }
  iw = Math.max(2, Math.floor(iw / 2) * 2); ih = Math.max(2, Math.floor(ih / 2) * 2)
  const fw = iw + 2 * b, fh = ih + 2 * b
  const m = o.frame === 'none' ? 0 : Math.max(4, Math.round(0.04 * fw))
  const labSize = o.label ? Math.max(18, Math.round(0.042 * OH)) : 0
  const lab = o.label ? Math.round(labSize * 1.5) : 0
  let cw = fw + 2 * m, ch = fh + 2 * m + lab
  cw += cw % 2; ch += ch % 2
  const fcx = Math.min(Math.max(o.pos.x * OW, fw / 2), OW - fw / 2)
  const fcy = Math.min(Math.max(o.pos.y * OH, fh / 2), OH - fh / 2)
  return { b, iw, ih, fw, fh, m, lab, labSize, cw, ch, ccx: fcx, ccy: fcy + (ch / 2 - (m + fh / 2)),
    radius: o.mode === 'pip' ? Math.round(0.05 * Math.min(fw, fh)) : 0 }
}
// Texto movido/escalado a mano: centro en pos (fracción del cuadro) y tamaño × scale (igual que en el render)
export function freeTextStyle(style, t) {
  const sc = Math.max(0.3, Math.min(Number(t.scale) || 1, 4))
  let out = style.replace(/font-size:calc\(var\(--k\)\*([\d.]+)px\)/, (m, n) => `font-size:calc(var(--k)*${(+n * sc).toFixed(1)}px)`)
  if (t.pos) {
    out = out.replace(/(^|;)\s*(top|bottom|left|right):[^;]*/g, '$1')
    out = out.replace(/transform:([^;]*)/, (m, tr) => `transform:translate(-50%,-50%) ${tr.replace(/translateX?\([^)]*\)/g, '').replace('none', '')}`)
    if (!/transform:/.test(out)) out += ';transform:translate(-50%,-50%)'
    out = `left:${(t.pos.x * 100).toFixed(2)}%;top:${(t.pos.y * 100).toFixed(2)}%;` + out
  }
  return out
}

export function fitText(text, fs, width, maxLines = 3, charW = 0.62) {
  const words = String(text).split(/\s+/).filter(Boolean)
  let size = fs
  for (;;) {
    const per = Math.max(4, Math.floor(width / (charW * size)))
    const lines = []
    let cur = ''
    for (const w of words) {
      const cand = (cur + ' ' + w).trim()
      if (cand.length <= per || !cur) cur = cand
      else { lines.push(cur); cur = w }
    }
    lines.push(cur)
    if ((lines.length <= maxLines && lines.every((x) => x.length <= per)) || size <= fs * 0.45) return { lines, size }
    size *= 0.92
  }
}

export function cardScale(o, lt, bd, isImage) {
  let f = 1
  if (o.anim === 'pop') f = lt < 0.18 ? 0.6 + 0.48 * lt / 0.18 : lt < 0.3 ? 1.08 - 0.08 * (lt - 0.18) / 0.12 : 1
  if (o.kenburns && isImage) f *= 1 + 0.05 * lt / Math.max(bd, 0.1)
  return f
}
const roundRect = (ctx, x, y, w, h, r) => {
  ctx.beginPath()
  if (r > 0 && ctx.roundRect) ctx.roundRect(x, y, w, h, r); else ctx.rect(x, y, w, h)
}
// recorte tipo "cover" (como ImageOps.fit): rellena el rectángulo destino sin deformar
const drawCover = (ctx, el, iw, ih, dx, dy, dw, dh) => {
  const r = Math.max(dw / iw, dh / ih), sw = dw / r, sh = dh / r
  ctx.drawImage(el, (iw - sw) / 2, (ih - sh) / 2, sw, sh, dx, dy, dw, dh)
}
const CARD_FONT = "'Anton', 'Impact', 'Arial Black', sans-serif"

// ---------- geometría (idéntica al servidor) ----------
/** Letra legible sobre la caja del título: si se parecen (blanco sobre blanco), negro o blanco. Igual que style.readable. */
export function readableOn(fg, bg) {
  const lum = (h) => {
    const m = /^#?([0-9a-f]{6})$/i.exec(h || '')
    if (!m) return 1
    const [r, g, b] = [0, 2, 4].map((i) => parseInt(m[1].slice(i, i + 2), 16) / 255)
      .map((c) => (c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b
  }
  const ct = (a, b) => (Math.max(lum(a), lum(b)) + 0.05) / (Math.min(lum(a), lum(b)) + 0.05)
  if (ct(fg, bg) >= 3) return fg
  return ct('#111111', bg) >= ct('#FFFFFF', bg) ? '#111111' : '#FFFFFF'
}

export function cropRect(sw, sh, target, cx, zoom = 1, cy = null) {
  let cw, ch
  if (sw / sh > target) { ch = sh; cw = sh * target } else { cw = sw; ch = sw / target }
  cw /= zoom; ch /= zoom
  const x = clamp(cx * sw - cw / 2, 0, sw - cw)
  const y = clamp(sh * (cy ?? (zoom > 1 ? 0.42 : 0.5)) - ch / 2, 0, sh - ch)
  return [cw, ch, x, y]
}

export function regionRect(sw, sh, target, r) {
  const rw = r.w * sw, rh = r.h * sh
  const cx = (r.x + r.w / 2) * sw, cy = (r.y + r.h / 2) * sh
  let cw, ch
  if (rw / rh > target) { cw = rw; ch = rw / target } else { cw = rh * target; ch = rh }
  cw = Math.min(cw, sw); ch = Math.min(ch, sh)
  return [cw, ch, clamp(cx - cw / 2, 0, sw - cw), clamp(cy - ch / 2, 0, sh - ch)]
}

/** Factor de zoom y desplazamiento de sacudida (px de salida a 1080) en el tiempo local t del segmento. */
export function motionAt(s, t) {
  let z = 1
  const ln = Math.max(0.1, segLen(s))
  const d = +s.drift || 0
  if (d) z += Math.abs(d) * (d > 0 ? Math.min(t / ln, 1) : Math.max(1 - t / ln, 0))
  let sx = 0, sy = 0
  for (const f of s.fx || []) {
    if (f.type === 'punch') {
      const t1 = f.t + PUNCH_IN + (f.hold ?? 1.1)
      const rise = 1 - Math.pow(1 - clamp((t - f.t) / PUNCH_IN, 0, 1), 3)
      const fl = clamp((t - t1) / PUNCH_OUT, 0, 1)
      z += f.amount * rise * (1 - fl * fl * (3 - 2 * fl))
    } else if (f.type === 'shake') {
      const env = Math.exp(-Math.pow((t - (f.t + 0.06)) / 0.13, 2))
      const amp = Math.min(16, f.amount * 70)
      sx += amp * (Math.sin(t * 37) + 0.45 * Math.sin(t * 59)) * env
      sy += amp * 0.7 * (Math.cos(t * 31) + 0.45 * Math.cos(t * 53)) * env
      z += (amp * 3.2 / 1080) * env
    }
  }
  return { z, sx, sy }
}

function trackX(track, srcT, fallback) {
  if (!track?.length) return fallback
  if (srcT <= track[0][0]) return track[0][1]
  for (let i = 1; i < track.length; i++) {
    const [tb, xb] = track[i]
    if (srcT < tb) {
      const [ta, xa] = track[i - 1]
      return xa + (xb - xa) * (srcT - ta) / Math.max(tb - ta, 0.01)
    }
  }
  return track[track.length - 1][1]
}

export function layoutOf(tl) {
  const l = { mode: 'crop', game_x: 0.5, ...(tl.layout || {}) }
  if (!['crop', 'blur', 'fit', 'split'].includes(l.mode)) l.mode = 'crop'
  if (l.mode === 'split' && !l.cam) l.mode = 'blur'
  return l
}

const CONNECTORS = new Set(['el', 'la', 'los', 'las', 'un', 'una', 'unos', 'unas', 'de', 'del', 'a', 'al', 'y', 'e', 'o', 'u',
  'que', 'en', 'con', 'por', 'para', 'mi', 'mis', 'tu', 'tus', 'su', 'sus', 'lo', 'le', 'les', 'se', 'no', 'ni', 'pero', 'como',
  'muy', 'más', 'es', 'the', 'an', 'of', 'to', 'and', 'in'])
const bare = (w) => w.toLowerCase().replace(/[^\wáéíóúñü]/g, '')

// igual que group_words() del backend: fin de frase, pausas, comas y sin palabras colgando
export function groupWords(words, maxWords = 3) {
  const groups = []
  let cur = []
  words.forEach((w, i) => {
    cur.push(w)
    const gap = i + 1 < words.length ? words[i + 1].start - w.end : 99
    const comma = /[,;:]$/.test(w.word) && cur.length >= 2
    if (cur.length >= maxWords || /[.!?…]$/.test(w.word) || comma || gap > 0.6) { groups.push(cur); cur = [] }
  })
  if (cur.length) groups.push(cur)
  const out = []
  let carry = []
  groups.forEach((g0, gi) => {
    const g = [...carry, ...g0]
    carry = []
    const nxt = groups[gi + 1]
    while (nxt && g.length > 1 && CONNECTORS.has(bare(g[g.length - 1].word)) && !/[.!?…,;:]$/.test(g[g.length - 1].word)
      && nxt[0].start - g[g.length - 1].end < 0.6) carry.unshift(g.pop())
    out.push(g)
  })
  if (carry.length) out.push(carry)
  return out
}

/** Distancia desde abajo de los subtítulos (px a resolución del timeline) — igual que caption_margin(). */
export function captionMargin(tl, st, capSize) {
  const W = tl.width, H = tl.height
  const pos = st.caption_position || 'auto'
  if (['bottom', 'middle', 'top'].includes(pos)) return H * { bottom: 0.27, middle: 0.45, top: 0.68 }[pos]
  if (W >= H) return H * 0.09
  const lay = layoutOf(tl)
  let center
  if (lay.mode === 'split') center = H * CAM_FRAC + capSize * 1.4
  else if (lay.mode === 'blur' || lay.mode === 'fit') center = (H + W / (lay.src_ar || 16 / 9)) / 2 + capSize * 1.3
  else return H * 0.28
  return Math.max(H * 0.08, H - center - capSize / 2)
}

// ---------- motor ----------
export class LiveEngine extends EventTarget {
  constructor({ srcUrl, kind = 'video', proxy }) {
    super()
    this.srcUrl = srcUrl
    this.kind = kind
    this.proxy = proxy // async () => ({url, offset}) cuando el navegador no puede leer el original
    this.offset = 0
    this.T = 0
    this._paused = true
    this._volume = 1
    this._muted = false
    this._rate = 1
    this.loop = false
    this.tl = null
    this.st = {}
    this.assets = {}
    this.bvideos = {}
    this.bgEls = {} // fondo del chroma por tramos: imágenes y clips por clave
    this.sfxPlayed = new Set()
    this.music = {}
    this.vids = [this._mkVideo(), this._mkVideo()]
    this.cur = 0
    this.error = null
    this._raf = null
    this._prevT = 0
  }

  _mkVideo() {
    const v = document.createElement(this.kind === 'audio' ? 'audio' : 'video')
    v.preload = 'auto'
    v.playsInline = true
    v.crossOrigin = 'anonymous'
    v.muted = true
    v.addEventListener('seeked', () => this.draw())
    v.addEventListener('loadeddata', () => this.draw())
    // un salto pedido antes de que el navegador abra el video se pierde: se repite al cargar
    v.addEventListener('loadedmetadata', () => { if (this.tl) { this._seekTo(this.T, true) } })
    v.addEventListener('error', () => this._onError(v))
    return v
  }

  async _onError(v) {
    if (this._triedProxy || !this.proxy) {
      this.error = 'Tu navegador no puede reproducir este video.'
      this.dispatchEvent(new Event('liveerror'))
      return
    }
    this._triedProxy = true
    this.status = 'Preparando una copia ligera para la vista en vivo…'
    this.dispatchEvent(new Event('livestatus'))
    try {
      const p = await this.proxy()
      this.offset = p.offset || 0
      for (const x of this.vids) { x.src = p.url; x.load() }
      this.status = null
      this.dispatchEvent(new Event('livestatus'))
      this._seekTo(this.T)
    } catch (e) {
      this.error = e.message || 'No se pudo preparar la vista en vivo.'
      this.dispatchEvent(new Event('liveerror'))
    }
  }

  mount(el) {
    window.__smcLive = this  // diagnóstico desde la consola
    this.root = el
    el.innerHTML = ''
    this.canvas = document.createElement('canvas')
    this.canvas.className = 'live-canvas'
    this.ctx = this.canvas.getContext('2d')
    this.small = document.createElement('canvas')
    this.sctx = this.small.getContext('2d')
    this.fgc = document.createElement('canvas') // pantalla verde: la persona se recorta aquí (resolución completa en pausa, media al reproducir)
    this.fgctx = this.fgc.getContext('2d', { willReadFrequently: true })
    this.overlay = document.createElement('div')
    this.overlay.className = 'live-overlay'
    el.append(this.canvas, this.overlay)
    for (const v of this.vids) if (!v.src) { v.src = this.srcUrl; v.load() }
    this._resize()
    this.draw()
  }

  /** Caja de un elemento movible en px del timeline (para arrastrarlo sobre la vista previa). */
  itemBox(id) {
    const tl = this.tl
    if (!tl) return null
    const b = tl.tracks.broll.find((x) => x.id === id)
    if (b) {
      const [el, isImg] = this._brollEl(b)
      const iw = el?.videoWidth || el?.naturalWidth, ih = el?.videoHeight || el?.naturalHeight
      if (!iw || !ih) return null
      const o = brollOpts(b, isImg)
      const g = cardGeometry(iw, ih, o, tl.width, tl.height)
      return { x: g.ccx - g.cw / 2, y: g.ccy - g.ch / 2, w: g.cw, h: g.ch, kind: 'broll' }
    }
    const node = this.overlay?.querySelector(`[data-id="${id}"]`)
    if (node && this.root) {
      const r = node.getBoundingClientRect(), R = this.root.getBoundingClientRect(), k = tl.width / Math.max(R.width, 1)
      return { x: (r.left - R.left) * k, y: (r.top - R.top) * k, w: r.width * k, h: r.height * k, kind: 'text' }
    }
    return null
  }

  setProject(tl, st, assetsById) {
    const first = !this.tl
    this.tl = tl
    this.st = st || {}
    this.assets = assetsById || {}
    if (first) this.dispatchEvent(new Event('loadedmetadata'))
    this.dispatchEvent(new Event('durationchange'))
    this._resize()
    if (this.T > tl.duration) this.T = tl.duration
    if (this._paused) this._seekTo(this.T)
    else this.draw()
  }

  _resize() {
    if (!this.canvas || !this.tl) return
    const k = 720 / Math.min(this.tl.width, this.tl.height) // vista previa a 720 px del lado corto
    this.cw = even(this.tl.width * k); this.ch = even(this.tl.height * k)
    if (this.canvas.width !== this.cw) { this.canvas.width = this.cw; this.canvas.height = this.ch }
    this.small.width = Math.max(16, Math.round(this.cw / 8)); this.small.height = Math.max(16, Math.round(this.ch / 8))
    this.root.style.aspectRatio = `${this.tl.width} / ${this.tl.height}`
  }

  // ----- API tipo <video> -----
  get duration() { return this.tl?.duration || 0 }
  get paused() { return this._paused }
  get currentTime() { return this.T }
  set currentTime(t) { this._seekTo(clamp(t, 0, this.duration)) }
  get volume() { return this._volume }
  set volume(x) { this._volume = x; this._applyVol() }
  get muted() { return this._muted }
  set muted(x) { this._muted = x; this._applyVol() }
  get playbackRate() { return this._rate }
  set playbackRate(r) { this._rate = r; for (const v of this.vids) v.playbackRate = r }
  get buffered() { return { length: 0, end: () => 0 } }

  _applyVol() {
    this.vids.forEach((v, i) => {
      v.muted = this._muted || i !== this.cur
      v.volume = clamp(this._volume * (this.st.voice_volume ?? 1), 0, 1)
    })
  }

  _segAt(t) {
    const segs = this.tl?.tracks.video || []
    for (let i = 0; i < segs.length; i++) {
      if (t < segs[i].start + segLen(segs[i]) - 1e-4) return [i, segs[i]]
    }
    return segs.length ? [segs.length - 1, segs[segs.length - 1]] : [-1, null]
  }

  _seekTo(t, force = false) {
    this.T = t
    const [i, s] = this._segAt(t)
    if (s) {
      const v = this.vids[this.cur]
      const want = s.src_in + (t - s.start) - this.offset
      if (force || Math.abs(v.currentTime - want) > 0.04) v.currentTime = Math.max(0, want)
      this._prepNext(i)
    }
    this._syncAux(true)
    this.draw()
    this.dispatchEvent(new Event('timeupdate'))
  }

  _prepNext(i) {
    const segs = this.tl.tracks.video
    const n = segs[i + 1]
    if (!n) return
    const nv = this.vids[1 - this.cur]
    const want = n.src_in - this.offset
    if (Math.abs(nv.currentTime - want) > 0.05) nv.currentTime = Math.max(0, want)
  }

  play() {
    if (!this.tl) return Promise.resolve()
    if (this.T >= this.duration - 0.05) this._seekTo(0)
    const [, s] = this._segAt(this.T)
    const v = this.vids[this.cur]
    if (s) {  // el video debe estar dentro de la toma actual antes de arrancar
      const want = s.src_in + (this.T - s.start) - this.offset
      if (Math.abs(v.currentTime - want) > 0.25) v.currentTime = Math.max(0, want)
    }
    this._paused = false
    this._applyVol()
    const p = v.play().catch(() => {})
    this._lastNow = performance.now()
    this._tick()
    this.dispatchEvent(new Event('play'))
    return p
  }

  pause() {
    this._paused = true
    for (const v of this.vids) v.pause()
    for (const a of Object.values(this.music)) a.pause()
    for (const v of Object.values(this.bvideos)) v.pause?.() // también hay imágenes (tarjetas): no tienen pause
    for (const v of Object.values(this.bgEls)) v.pause?.()
    if (this._ckEl?.pause) this._ckEl.pause()
    cancelAnimationFrame(this._raf)
    this.dispatchEvent(new Event('pause'))
    this.draw()
  }

  _tick = () => {
    if (this._paused) return
    const [i, s] = this._segAt(this.T)
    const v = this.vids[this.cur]
    if (s) {
      if (this.kind !== 'video' && this.tl.tracks.video.length <= 1) {
        this.T = clamp(v.currentTime + this.offset - s.src_in + s.start, 0, this.duration)
      } else {
        this.T = s.start + clamp(v.currentTime + this.offset - s.src_in, 0, segLen(s))
      }
      const end = s.src_out - this.offset
      if (v.currentTime >= end - 0.02 || v.ended) {
        const segs = this.tl.tracks.video
        const n = segs[i + 1]
        if (!n) {
          if (this.loop) { this._seekTo(0); v.play().catch(() => {}) } else { this.T = this.duration; this.pause(); this.dispatchEvent(new Event('ended')) }
        } else if (Math.abs(n.src_in - s.src_out) < 0.05) {
          // continuo: seguimos con el mismo video
        } else {
          const nv = this.vids[1 - this.cur]
          const want = n.src_in - this.offset
          if (!nv.seeking && Math.abs(nv.currentTime - want) > 0.15) nv.currentTime = Math.max(0, want)
          if (nv.seeking || nv.readyState < 2) {
            // La siguiente toma aún no está lista: se congela un instante el último cuadro en vez de cambiar a un
            // video que todavía muestra cuadros viejos (eso se veía como partes repetidas).
            v.pause()
            this.T = n.start - 0.001
          } else {
            v.pause()
            this.cur = 1 - this.cur
            this._applyVol()
            nv.play().catch(() => {})
            this._prepNextLater = i + 1
            setTimeout(() => { if (this.tl) this._prepNext(this._prepNextLater) }, 60)
            this.T = n.start
          }
        }
      }
    }
    this._syncAux(false)
    this.draw()
    this.dispatchEvent(new Event('timeupdate'))
    this._raf = requestAnimationFrame(this._tick)
  }

  // música, efectos de sonido y videos de b-roll
  _syncAux(seeking) {
    const T = this.T, tl = this.tl
    if (!tl) return
    const vol = this._muted ? 0 : this._volume
    const talking = (tl.words || []).some((w) => w.start - 0.1 <= T && T <= w.end + 0.25)
    for (const m of tl.tracks.music) {
      let a = this.music[m.id]
      const asset = this.assets[m.asset_id]
      if (!asset) continue
      if (!a) { a = this.music[m.id] = new Audio(asset.url); a.loop = true; a.preload = 'auto' }
      const inside = T >= m.start && T < m.end
      const duck = talking && this.st.duck_music !== false ? 0.35 : 1
      a.volume = clamp((m.volume ?? 0.15) * vol * duck * 2.2, 0, 1)
      if (inside && !this._paused) {
        const want = ((T - m.start) % (a.duration || 1e9))
        if (a.paused || seeking || Math.abs(a.currentTime - want) > 0.5) a.currentTime = want
        if (a.paused) a.play().catch(() => {})
      } else if (!a.paused) a.pause()
    }
    if (seeking) this.sfxPlayed.clear()
    if (!this._paused && !seeking) {
      for (const x of tl.tracks.sfx) {
        if (this.sfxPlayed.has(x.id)) continue
        if (x.start <= T && x.start > this._prevT - 0.05 && T - x.start < 0.3) {
          const asset = this.assets[x.asset_id]
          this.sfxPlayed.add(x.id)
          if (asset && vol > 0) { const a = new Audio(asset.url); a.volume = clamp((x.volume ?? 0.5) * vol, 0, 1); a.play().catch(() => {}) }
        }
      }
    }
    this._prevT = T
    this._syncChromaBg(seeking)
    this._syncBgSched(seeking)
    for (const b of tl.tracks.broll) {
      const asset = this.assets[b.asset_id]
      const isVid = b.video_id || asset?.media_type === 'video'
      if (!isVid) continue
      let el = this.bvideos[b.id]
      if (el && el.tagName !== 'VIDEO') el = null // el recurso cambió de imagen a video
      const url = b.video_id ? `/api/library/${b.video_id}/stream` : asset?.url
      if (!el && url) {
        el = this.bvideos[b.id] = document.createElement('video')
        el.src = url; el.muted = true; el.preload = 'auto'; el.playsInline = true
        el.addEventListener('seeked', () => this.draw())
      }
      if (!el) continue
      const inside = T >= b.start && T < b.end
      const want = (b.src_in || 0) + (T - b.start)
      if (inside) {
        if (seeking || Math.abs(el.currentTime - want) > 0.4) el.currentTime = want
        if (!this._paused && el.paused) el.play().catch(() => {})
      } else if (!el.paused) el.pause()
    }
  }

  _chromaBgEl() {
    const ck = this.tl?.chroma
    if (!ck?.enabled) return null
    const bg = ck.bg || {}
    let url = null, img = false
    if (bg.kind === 'video' && bg.video_id) url = `/api/library/${bg.video_id}/stream`
    else if (bg.kind === 'asset' && bg.asset_id) { const a = this.assets[bg.asset_id]; url = a?.url; img = a?.media_type === 'image' }
    if (!url) return null
    if (this._ckUrl !== url) {
      this._ckUrl = url
      if (img) { this._ckEl = new Image(); this._ckEl.onload = () => this.draw() } else {
        this._ckEl = document.createElement('video')
        Object.assign(this._ckEl, { muted: true, preload: 'auto', playsInline: true, loop: true })
        this._ckEl.crossOrigin = 'anonymous'
        this._ckEl.addEventListener('seeked', () => this.draw())
        this._ckEl.addEventListener('loadeddata', () => { this._syncChromaBg(true); this.draw() })
      }
      this._ckEl.src = url
    }
    return this._ckEl
  }

  _syncChromaBg(seeking) {
    const el = this._chromaBgEl()
    if (!el || el.tagName !== 'VIDEO' || !el.duration) return
    const bg = this.tl.chroma.bg || {}
    el.volume = clamp((bg.volume || 0) * (this._muted ? 0 : this._volume), 0, 1)
    el.muted = !(bg.volume > 0) || this._muted
    const want = ((bg.start || 0) + this.T) % el.duration
    if (seeking || Math.abs(el.currentTime - want) > 0.5) el.currentTime = want
    if (!this._paused && el.paused) el.play().catch(() => {})
    if (this._paused && !el.paused) el.pause()
  }

  // Recorte por color como el chromakey de FFmpeg (distancia en Cb/Cr), con quitado de reflejo verde
  _keyOut(ctx, w, h, ck) {
    const hex = (ck.color || '#00FF00').replace('#', '')
    const kr = parseInt(hex.slice(0, 2), 16), kg = parseInt(hex.slice(2, 4), 16), kb = parseInt(hex.slice(4, 6), 16)
    const kcb = -0.168736 * kr - 0.331264 * kg + 0.5 * kb, kcr = 0.5 * kr - 0.418688 * kg - 0.081312 * kb
    const sim = ck.similarity ?? 0.14, blend = ck.blend ?? 0.08
    const greenish = kg > kr && kg > kb, bluish = kb > kr && kb > kg
    const despill = ck.despill !== false
    const im = ctx.getImageData(0, 0, w, h), d = im.data
    for (let p = 0; p < d.length; p += 4) {
      const r = d[p], g = d[p + 1], b = d[p + 2]
      const du = (-0.168736 * r - 0.331264 * g + 0.5 * b) - kcb, dv = (0.5 * r - 0.418688 * g - 0.081312 * b) - kcr
      const diff = Math.sqrt((du * du + dv * dv) / 130050)
      let a = blend > 0.0001 ? (diff - sim) / blend : (diff > sim ? 1 : 0)
      a = a < 0 ? 0 : a > 1 ? 1 : a
      d[p + 3] = Math.round(d[p + 3] * a)
      if (despill && a > 0) {
        if (greenish) { const m = Math.max(r, b); if (g > m) d[p + 1] = m + (g - m) * 0.5 }
        else if (bluish) { const m = Math.max(r, g); if (b > m) d[p + 2] = m + (b - m) * 0.5 }
      }
    }
    ctx.putImageData(im, 0, 0)
  }

  // ----- dibujo -----
  draw() {
    const ctx = this.ctx, tl = this.tl
    if (!ctx || !tl) return
    const W = this.cw, H = this.ch, k = W / tl.width
    const [i, s] = this._segAt(this.T)
    ctx.save()
    ctx.fillStyle = '#000'
    ctx.fillRect(0, 0, W, H)
    const v = this.vids[this.cur]
    const ck = this.tl.chroma?.enabled ? this.tl.chroma : null
    if (this.kind === 'video' && s && v.readyState >= 2 && ck) {
      this._drawChromaBg(ctx, W, H, ck)
      const q = this._paused ? 1 : 0.5 // en pausa se ve nítido; al reproducir se recorta a media resolución para ir fluido
      const fw = Math.round(W * q), fh = Math.round(H * q)
      if (this.fgc.width !== fw || this.fgc.height !== fh) { this.fgc.width = fw; this.fgc.height = fh }
      const f = this.fgctx
      f.clearRect(0, 0, fw, fh)
      this._drawSeg(f, v, s, this.T - s.start, fw, fh, k * q, true)
      this._keyOut(f, fw, fh, ck)
      if (ck.protect?.length && v.videoWidth) { // recuadro de tu cámara: se repone sin recortar (sigue al zoom)
        this._clipSrc = ck.protect.map((r) => [r.x * v.videoWidth, r.y * v.videoHeight, r.w * v.videoWidth, r.h * v.videoHeight])
        this._drawSeg(f, v, s, this.T - s.start, fw, fh, k * q, true)
        this._clipSrc = null
      }
      const sub = { scale: 1, x: 0.5, y: 1, ...(ck.subject || {}) }
      const dw = W * sub.scale, dh = H * sub.scale
      ctx.drawImage(this.fgc, (W - dw) * sub.x, (H - dh) * sub.y, dw, dh)
    } else if (this.kind === 'video' && s && v.readyState >= 2) {
      this._drawSeg(ctx, v, s, this.T - s.start, W, H, k)
    } else if (this.kind !== 'video') {
      const g = ctx.createLinearGradient(0, 0, W, H)
      g.addColorStop(0, this.st.bg_color_1 || '#1d1b4b'); g.addColorStop(1, this.st.bg_color_2 || '#5b21b6')
      ctx.fillStyle = g; ctx.fillRect(0, 0, W, H)
    }
    this._drawBroll(ctx, W, H)
    if (s && i > 0 && (s.transition === 'flash' || s.transition === 'fade')) {
      const lt = this.T - s.start
      const a = s.transition === 'flash' ? clamp(1 - lt / 0.18, 0, 1) * 0.85 : clamp(1 - lt / 0.3, 0, 1)
      if (a > 0) { ctx.fillStyle = s.transition === 'flash' ? `rgba(255,255,255,${a})` : `rgba(0,0,0,${a})`; ctx.fillRect(0, 0, W, H) }
    }
    ctx.restore()
    this._drawOverlay(k)
  }

  _drawSeg(ctx, v, s, lt, W, H, k, keyed = false) {
    const sw = v.videoWidth, sh = v.videoHeight
    if (!sw || !sh) return
    const target = W / H
    const lay = layoutOf(this.tl)
    let mode = ['crop', 'blur', 'fit', 'split'].includes(s.layout) ? s.layout : lay.mode
    if (keyed && (mode === 'blur' || mode === 'split')) mode = 'fit' // con pantalla verde el fondo lo pone el chroma
    const m = motionAt(s, lt)
    const srcT = s.src_in + lt
    // recorte con movimiento: el zoom reduce el área de origen alrededor del centro
    const drawMoved = (sx, sy, swid, shei, dx, dy, dw, dh) => {
      const zw = swid / m.z, zh = shei / m.z
      const ox = (m.sx * k) * (swid / dw) / m.z, oy = (m.sy * k) * (shei / dh) / m.z
      const x = clamp(sx + (swid - zw) / 2 + ox, 0, sw - zw), y = clamp(sy + (shei - zh) / 2 + oy, 0, sh - zh)
      if (!this._clipSrc) { ctx.drawImage(v, x, y, zw, zh, dx, dy, dw, dh); return }
      const fx = dw / zw, fy = dh / zh
      for (const [px, py, pw, ph] of this._clipSrc) { // solo la parte de cada zona protegida que se ve
        const ix = Math.max(x, px), iy = Math.max(y, py), ix2 = Math.min(x + zw, px + pw), iy2 = Math.min(y + zh, py + ph)
        if (ix2 - ix < 1 || iy2 - iy < 1) continue
        ctx.drawImage(v, ix, iy, ix2 - ix, iy2 - iy, dx + (ix - x) * fx, dy + (iy - y) * fy, (ix2 - ix) * fx, (iy2 - iy) * fy)
      }
    }
    if (s.rect) {
      const [cw, ch, x, y] = regionRect(sw, sh, target, s.rect)
      drawMoved(x, y, cw, ch, 0, 0, W, H)
    } else if (mode === 'crop') {
      const cx = trackX(s.track, srcT, s.crop_x ?? 0.5)
      const [cw, ch, x, y] = cropRect(sw, sh, target, cx, s.zoom || 1, s.crop_y ?? null)
      drawMoved(x, y, cw, ch, 0, 0, W, H)
    } else if (mode === 'split') {
      const camH = even(H * CAM_FRAC), gameH = H - camH
      const c = lay.cam
      const cx = c.x * sw, cy = c.y * sh, cw = c.w * sw, ch = c.h * sh
      // cámara: cubre 1080x680 recortando lo que sobre
      const tr = W / camH
      let uw = cw, uh = cw / tr
      if (uh > ch) { uh = ch; uw = ch * tr }
      ctx.drawImage(v, cx + (cw - uw) / 2, cy + (ch - uh) / 2, uw, uh, 0, 0, W, camH)
      const gw = Math.min(sw, sh * W / gameH)
      const gx = clamp((lay.game_x ?? 0.5) * sw - gw / 2, 0, sw - gw)
      ctx.save(); ctx.beginPath(); ctx.rect(0, camH, W, gameH); ctx.clip()
      drawMoved(gx, 0, gw, sh, 0, camH, W, gameH)
      ctx.restore()
    } else {
      const r = Math.min(W / sw, H / sh)
      const fw = sw * r, fh = sh * r
      const z = Math.max(1, s.zoom || 1)
      const zw = fw * z, zh = fh * z
      const vw = Math.min(W, zw), vh = Math.min(H, zh)
      if (mode === 'blur') {
        const S = this.small, sc = this.sctx
        const br = Math.max(S.width / sw, S.height / sh)
        sc.drawImage(v, (S.width - sw * br) / 2, (S.height - sh * br) / 2, sw * br, sh * br)
        ctx.filter = 'blur(6px) brightness(0.9) saturate(1.15)'
        ctx.drawImage(S, -8, -8, W + 16, H + 16)
        ctx.filter = 'none'
      }
      // imagen principal: área visible del original (con zoom del segmento y encuadre horizontal)
      const visW = sw * (vw / zw), visH = sh * (vh / zh)
      const vx = clamp((s.crop_x ?? 0.5) * sw - visW / 2, 0, sw - visW)
      const dx = (W - vw) / 2, dy = (H - vh) / 2
      ctx.save(); ctx.beginPath(); ctx.rect(dx, dy, vw, vh); ctx.clip()
      drawMoved(vx, (sh - visH) / 2, visW, visH, dx, dy, vw, vh)
      ctx.restore()
    }
  }

  _brollEl(b) {
    const asset = this.assets[b.asset_id]
    let el = this.bvideos[b.id]
    if (!el && asset?.media_type === 'image') {
      el = this.bvideos[b.id] = new Image()
      el.src = asset.url
      el.onload = () => this.draw()
    }
    return [el, asset?.media_type === 'image']
  }

  _drawBroll(ctx, W, H) {
    const T = this.T, k = W / this.tl.width
    for (const b of this.tl.tracks.broll) {
      if (T < b.start || T >= b.end) continue
      const [el, isImg] = this._brollEl(b)
      const iw = el?.videoWidth || el?.naturalWidth, ih = el?.videoHeight || el?.naturalHeight
      if (!iw || !ih) continue
      const o = brollOpts(b, isImg), lt = T - b.start, bd = b.end - b.start
      if (o.mode === 'full') {
        const r = b.fit === 'blur' ? Math.min(W / iw, H / ih) : Math.max(W / iw, H / ih)
        if (b.fit === 'blur') { ctx.fillStyle = '#111'; ctx.fillRect(0, 0, W, H) }
        const z = o.kenburns && isImg ? 1 + 0.05 * lt / Math.max(bd, 0.1) : 1
        ctx.drawImage(el, (W - iw * r * z) / 2, (H - ih * r * z) / 2, iw * r * z, ih * r * z)
        continue
      }
      this._drawCard(ctx, el, iw, ih, isImg, o, lt, bd, k)
    }
  }

  _drawCard(ctx, el, iw, ih, isImg, o, lt, bd, k) {
    const tl = this.tl, OW = tl.width, OH = tl.height
    const g = cardGeometry(iw, ih, o, OW, OH)
    const sc = cardScale(o, lt, bd, isImg)
    let x = g.ccx, y = g.ccy
    const e = Math.pow(1 - clamp(lt / 0.3, 0, 1), 2)
    const hw = g.cw * sc / 2, hh = g.ch * sc / 2
    if (o.anim === 'slide_left') x += e * (OW - (x - hw))
    else if (o.anim === 'slide_right') x -= e * (x + hw)
    else if (o.anim === 'slide_up') y += e * (OH - (y - hh))
    let alpha = lt > bd - 0.15 ? clamp((bd - lt) / 0.15, 0, 1) : 1
    if (o.anim === 'fade') alpha *= clamp(lt / 0.25, 0, 1)
    if (alpha <= 0) return
    ctx.save()
    ctx.globalAlpha = alpha
    ctx.translate(x * k, y * k)
    ctx.rotate(o.rot * Math.PI / 180)
    ctx.scale(sc * k, sc * k)
    const fx = -g.cw / 2 + g.m, fy = -g.ch / 2 + g.m // marco, en px de salida
    if (o.frame !== 'none') {
      ctx.save()
      ctx.shadowColor = 'rgba(0,0,0,0.55)'; ctx.shadowBlur = 0.044 * g.fw * sc * k
      ctx.shadowOffsetX = 0.012 * g.fw * sc * k; ctx.shadowOffsetY = 0.02 * g.fw * sc * k
      ctx.fillStyle = g.b ? '#fff' : '#000'
      roundRect(ctx, fx, fy, g.fw, g.fh, g.radius); ctx.fill()
      ctx.restore()
    }
    ctx.save()
    roundRect(ctx, fx + g.b, fy + g.b, g.iw, g.ih, g.radius); ctx.clip()
    drawCover(ctx, el, iw, ih, fx + g.b, fy + g.b, g.iw, g.ih)
    ctx.restore()
    if (o.label) {
      let size = g.labSize
      ctx.font = `bold ${size}px ${CARD_FONT}`
      while (size > 14 && ctx.measureText(o.label).width > g.cw * 0.98) { size = Math.floor(size * 0.9); ctx.font = `bold ${size}px ${CARD_FONT}` }
      ctx.textAlign = 'center'; ctx.textBaseline = 'top'
      const ty = fy + g.fh + Math.max(2, Math.round(size * 0.18))
      ctx.lineWidth = Math.max(2, size / 10) * 2; ctx.strokeStyle = '#000'; ctx.lineJoin = 'round'
      ctx.strokeText(o.label, 0, ty); ctx.fillStyle = '#fff'; ctx.fillText(o.label, 0, ty)
    }
    ctx.restore()
  }

  // ----- fondo del chroma por tramos -----
  _bgSched() {
    const s = this.tl?.chroma?.bg_schedule
    return Array.isArray(s) ? s : []
  }

  _bgEntryAt(T) {
    const s = this._bgSched()
    for (let i = 0; i < s.length; i++) if (T >= s[i].start && T < s[i].end) return [i, s[i]]
    return [-1, null]
  }

  _bgEl(e) {
    if (!e || e.kind === 'text') return null
    let url = null, img = false
    if (e.video_id) url = `/api/library/${e.video_id}/stream`
    else { const a = this.assets[e.asset_id]; url = a?.url; img = a?.media_type === 'image' }
    if (!url) return null
    let el = this.bgEls[url]
    if (!el) {
      if (img) { el = new Image(); el.onload = () => this.draw() } else {
        el = document.createElement('video')
        Object.assign(el, { muted: true, preload: 'auto', playsInline: true })
        el.crossOrigin = 'anonymous'
        el.addEventListener('seeked', () => this.draw())
      }
      el.src = url
      this.bgEls[url] = el
    }
    return el
  }

  _syncBgSched(seeking) {
    const [, e] = this._bgEntryAt(this.T)
    const cur = this._bgEl(e)
    for (const el of Object.values(this.bgEls)) {
      if (el.tagName !== 'VIDEO') continue
      if (el === cur) {
        const want = (e.src_in || 0) + (this.T - e.start)
        if (seeking || Math.abs(el.currentTime - want) > 0.4) el.currentTime = Math.min(want, Math.max(0, (el.duration || 1e9) - 0.05))
        if (!this._paused && el.paused) el.play().catch(() => {})
        if (this._paused && !el.paused) el.pause()
      } else if (!el.paused) el.pause()
    }
  }

  _drawBgEntry(ctx, e, W, H) {
    if (!e) return false
    const col = e.bg_color || (e.kind === 'text' ? '#E8424F' : '#101018')
    if (e.kind === 'text') {
      ctx.fillStyle = col; ctx.fillRect(0, 0, W, H)
      const txt = String(e.text || '').toUpperCase()
      let size = Math.round(H * 0.24)
      ctx.font = `${size}px ${CARD_FONT}`
      while (size > 24 && ctx.measureText(txt).width > W * 0.86) { size = Math.floor(size * 0.9); ctx.font = `${size}px ${CARD_FONT}` }
      ctx.fillStyle = e.color || '#fff'; ctx.textAlign = 'center'; ctx.textBaseline = 'middle'
      const sub = e.sub || ''
      const ssize = Math.round(size * 0.32)
      ctx.fillText(txt, W / 2, H / 2 - (sub ? ssize * 0.8 : 0))
      if (sub) { ctx.font = `bold ${ssize}px ${CARD_FONT}`; ctx.fillText(sub, W / 2, H / 2 + size * 0.45 + ssize * 0.2) }
      return true
    }
    const el = this._bgEl(e)
    const iw = el?.videoWidth || el?.naturalWidth, ih = el?.videoHeight || el?.naturalHeight
    if (!iw || !ih) { ctx.fillStyle = col; ctx.fillRect(0, 0, W, H); return true }
    const isImg = el.tagName !== 'VIDEO'
    const kb = ('kenburns' in e ? e.kenburns : isImg) ? 1 + 0.05 * (this.T - e.start) / Math.max(e.end - e.start, 0.1) : 1
    ctx.save()
    if (e.fit === 'contain') {
      ctx.fillStyle = col; ctx.fillRect(0, 0, W, H)
      const r = Math.min(W * 0.84 / iw, H * 0.84 / ih) * kb
      ctx.drawImage(el, (W - iw * r) / 2, (H - ih * r) / 2, iw * r, ih * r)
    } else {
      const r = Math.max(W / iw, H / ih) * kb
      ctx.drawImage(el, (W - iw * r) / 2, (H - ih * r) / 2, iw * r, ih * r)
    }
    ctx.restore()
    return true
  }

  _drawDefaultBg(ctx, W, H, ck) {
    const bgc = ck.bg || {}
    if (bgc.kind === 'color') { ctx.fillStyle = bgc.color || '#101018'; ctx.fillRect(0, 0, W, H) }
    const el = this._chromaBgEl()
    const iw = el?.videoWidth || el?.naturalWidth, ih = el?.videoHeight || el?.naturalHeight
    if (iw && ih) { const r = Math.max(W / iw, H / ih); ctx.drawImage(el, (W - iw * r) / 2, (H - ih * r) / 2, iw * r, ih * r) }
  }

  _drawChromaBg(ctx, W, H, ck) {
    const [i, e] = this._bgEntryAt(this.T)
    if (!e) { this._drawDefaultBg(ctx, W, H, ck); return }
    const lt = this.T - e.start
    const prev = i > 0 && Math.abs(this._bgSched()[i - 1].end - e.start) < 0.05 ? this._bgSched()[i - 1] : null
    if ((e.anim === 'fade' || e.anim === 'slide') && lt < 0.25) { // entrada corta sobre lo anterior
      const p = clamp(lt / 0.25, 0, 1)
      if (e.anim === 'slide') {
        ctx.save(); ctx.translate(-W * p, 0)
        if (!this._drawBgEntry(ctx, prev, W, H)) this._drawDefaultBg(ctx, W, H, ck)
        ctx.restore(); ctx.save(); ctx.translate(W * (1 - p), 0); this._drawBgEntry(ctx, e, W, H); ctx.restore()
      } else {
        if (!this._drawBgEntry(ctx, prev, W, H)) this._drawDefaultBg(ctx, W, H, ck)
        ctx.save(); ctx.globalAlpha = p; this._drawBgEntry(ctx, e, W, H); ctx.restore()
      }
      return
    }
    this._drawBgEntry(ctx, e, W, H)
  }

  _drawOverlay(k) {
    const tl = this.tl, st = this.st, T = this.T
    const o = this.overlay
    if (!o) return
    const cssK = (this.root.clientWidth || this.cw) / tl.width // px de pantalla por px del timeline
    o.style.setProperty('--k', cssK)
    const parts = []
    const esc = (x) => String(x).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]))
    const capSize = (st.caption_size || 88) * Math.min(tl.width, tl.height) / 1080
    for (const t of tl.tracks.text) {
      if (T < t.start || T >= t.end) continue
      const kind = ['title', 'callout', 'lower', 'meme'].includes(t.kind) ? t.kind : 'callout'
      const text = kind === 'lower' ? t.text : String(t.text).toUpperCase()
      const age = T - t.start
      let style = ''
      if (kind === 'meme') {
        const rot = [-6, 5, -3, 7][text.length % 4], yy = [0.30, 0.62, 0.40][text.length % 3]
        const sc = age < 0.12 ? 1.6 - age / 0.12 * 0.68 : age < 0.22 ? 0.92 + (age - 0.12) / 0.1 * 0.12 : age < 0.3 ? 1.04 - (age - 0.22) / 0.08 * 0.04 : 1
        style = `top:${yy * 100}%;transform:translate(-50%,-50%) rotate(${rot}deg) scale(${sc});font-size:calc(var(--k)*${capSize * 1.9}px);color:${st.callout_color || '#FFE500'}`
      } else if (kind === 'callout') {
        const sc = age < 0.14 ? 0.7 + age / 0.14 * 0.38 : age < 0.24 ? 1.08 - (age - 0.14) / 0.1 * 0.08 : 1
        const split = layoutOf(tl).mode === 'split' && tl.width < tl.height
        style = `top:${split ? 60 : 36}%;transform:translate(-50%,-50%) scale(${sc});font-size:calc(var(--k)*${capSize * 1.25}px);color:${st.callout_color || '#FFE500'};font-family:'${st.title_font || 'Arial Black'}'`
      } else if (kind === 'title' && layoutOf(tl).mode === 'split' && tl.width < tl.height) {
        style = `bottom:calc(100% - var(--k)*${tl.height * CAM_FRAC - 28}px);transform:translateX(-50%);font-size:calc(var(--k)*66px);color:${readableOn(st.title_color || '#111', st.title_bg || '#FFE500')};background:${st.title_bg || '#FFE500'};font-family:'${st.title_font || 'Arial Black'}'`
      } else if (kind === 'title') {
        style = `top:12%;transform:translateX(-50%);font-size:calc(var(--k)*66px);color:${readableOn(st.title_color || '#111', st.title_bg || '#FFE500')};background:${st.title_bg || '#FFE500'};font-family:'${st.title_font || 'Arial Black'}'`
      } else {
        style = `bottom:20%;left:6%;transform:none;font-size:calc(var(--k)*48px);color:${readableOn(st.title_color || '#111', st.title_bg || '#FFE500')};background:${st.title_bg || '#FFE500'};font-family:'${st.title_font || 'Arial Black'}'`
      }
      if (t.pos || (t.scale && t.scale !== 1)) style = freeTextStyle(style, t)
      // que quepa en el cuadro: líneas y tamaño como en el render (render.fit_text)
      const fsM = style.match(/font-size:calc\(var\(--k\)\*([\d.]+)px\)/)
      let html = esc(text)
      if (fsM) {
        const avail = tl.width * (kind === 'lower' ? 0.80 : 0.88) - (kind === 'title' || kind === 'lower' ? 40 * Math.min(tl.width, tl.height) / 1080 : 0)
        const f = fitText(text, +fsM[1], avail, kind === 'lower' ? 2 : 3, kind === 'meme' ? 0.55 : 0.70)
        style = style.replace(fsM[0], `font-size:calc(var(--k)*${f.size.toFixed(1)}px)`)
        html = f.lines.map(esc).join('<br>')
      }
      parts.push(`<div class="lv-text lv-${kind}" data-id="${t.id}" style="${style}">${html}</div>`)
    }
    if (tl.captions?.enabled !== false && st.caption_style && st.caption_style !== 'none' && tl.words?.length) {
      if (!this._groups || this._groupsSrc !== tl.words || this._groupsN !== st.caption_max_words) {
        this._groups = groupWords(tl.words, st.caption_max_words || 3)
        this._groupsSrc = tl.words; this._groupsN = st.caption_max_words
      }
      const g = this._groups.find((x) => x[0].start <= T && T < x[x.length - 1].end + 0.05)
      if (g) {
        const up = st.caption_uppercase !== false && st.caption_style !== 'clean'
        const enter = T - g[0].start
        const sc = enter < 0.09 ? 0.86 + enter / 0.09 * 0.14 : 1
        const ws = g.map((w, j) => {
          const txt = esc(up ? w.word.toUpperCase() : w.word)
          const nextStart = j + 1 < g.length ? g[j + 1].start : g[g.length - 1].end + 0.05
          const hl = st.caption_style === 'karaoke' || st.caption_style === 'clean'
          const on = hl && (j ? w.start : g[0].start) <= T && T < nextStart
          return on ? `<span class="lv-on" style="color:${st.highlight_color || '#FFE500'}">${txt}</span>` : txt
        }).join(' ')
        const mb = captionMargin(tl, st, capSize)
        const clean = st.caption_style === 'clean'
        const font = clean ? 'Segoe UI, Arial' : `'${st.caption_font || 'Arial Black'}'`
        parts.push(`<div class="lv-cap${clean ? ' lv-clean' : ''}" style="bottom:calc(var(--k)*${mb}px);font-size:calc(var(--k)*${capSize}px);color:${st.caption_color || '#fff'};font-family:${font};transform:translateX(-50%)${clean ? '' : ` scale(${sc})`}">${ws}</div>`)
      }
    }
    const html = parts.join('')
    if (html !== this._lastHtml) { o.innerHTML = html; this._lastHtml = html }
  }

  destroy() {
    if (this._ckEl?.pause) this._ckEl.pause()
    this.pause()
    for (const v of [...this.vids, ...Object.values(this.bvideos), ...Object.values(this.bgEls)]) { try { v.removeAttribute('src'); v.load?.() } catch { /* */ } }
    for (const a of Object.values(this.music)) a.pause()
    this.tl = null
  }
}
