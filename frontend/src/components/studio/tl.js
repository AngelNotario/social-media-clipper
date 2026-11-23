// Utilidades del timeline en el navegador (el servidor vuelve a validar y normalizar al guardar).

export const clone = (x) => JSON.parse(JSON.stringify(x))
export const segLen = (s) => s.src_out - s.src_in
export const uid = (p) => `${p}_${Math.random().toString(16).slice(2, 8)}`

export function relayout(tl) {
  let t = 0
  for (const s of tl.tracks.video) { s.start = +t.toFixed(3); t += segLen(s) }
  tl.duration = +t.toFixed(3)
  return tl
}

/** Desplaza superposiciones que empiezan en/después de `after` (ripple). */
export function rippleShift(tl, after, delta) {
  for (const track of ['broll', 'text', 'music', 'sfx']) {
    for (const it of tl.tracks[track]) {
      if (it.start >= after - 0.001) {
        it.start = Math.max(0, it.start + delta)
        if ('end' in it) it.end = Math.max(it.start + 0.1, it.end + delta)
      }
    }
  }
}

// Lista de una pista. "bg" = fondo por tramos que pone la IA detrás de la cámara (chroma.bg_schedule).
export function trackList(tl, track) {
  if (track === 'bg') return (tl.chroma && tl.chroma.bg_schedule) || []
  return tl.tracks[track] || []
}

export function findItem(tl, id) {
  for (const track of ['video', 'broll', 'text', 'music', 'sfx', 'bg']) {
    const it = trackList(tl, track).find((x) => x.id === id)
    if (it) return { track, item: it }
  }
  return { track: null, item: null }
}

export function deleteItem(tl, id) {
  const { track, item } = findItem(tl, id)
  if (!track) return tl
  if (track === 'video') {
    if (tl.tracks.video.length <= 1) return tl
    const len = segLen(item)
    const end = item.start + len
    tl.tracks.video = tl.tracks.video.filter((x) => x.id !== id)
    // superposiciones completamente dentro del segmento borrado se eliminan
    for (const t of ['broll', 'text', 'sfx']) {
      tl.tracks[t] = tl.tracks[t].filter((x) => !(x.start >= item.start && (x.end ?? x.start) <= end))
    }
    rippleShift(tl, end, -len)
    relayout(tl)
  } else if (track === 'bg') {
    tl.chroma.bg_schedule = tl.chroma.bg_schedule.filter((x) => x.id !== id)
  } else {
    tl.tracks[track] = tl.tracks[track].filter((x) => x.id !== id)
  }
  return tl
}

// Los fondos antiguos de la IA no traían id: se les asigna uno para poder seleccionarlos.
export function ensureBgIds(tl) {
  const b = tl.chroma && tl.chroma.bg_schedule
  if (b) b.forEach((e, i) => { if (!e.id) e.id = `bg_${i}_${Math.round((e.start || 0) * 100)}` })
  return tl
}

export function captionGroups(words, maxWords = 3) {
  const groups = []
  let cur = []
  words.forEach((w, i) => {
    cur.push(w)
    const gap = i + 1 < words.length ? words[i + 1].start - w.end : 99
    if (cur.length >= maxWords || /[.!?…,;:]$/.test(w.word) || gap > 0.6) { groups.push(cur); cur = [] }
  })
  if (cur.length) groups.push(cur)
  return groups.map((g) => ({ start: g[0].start, end: g[g.length - 1].end, text: g.map((w) => w.word).join(' ') }))
}
