import { useEffect, useState } from 'react'
import { api } from '../../api.js'

// Qué tipo de video es y cómo editarlo: lo usa la auto-edición (ritmo, subtítulos, música, chroma, b-roll e instrucciones).
export const EMPTY_BRIEF = { video_type: null, edit_style: null, learned_style_id: null, captions: 'auto', caption_look: 'auto',
  music: 'auto', chroma: 'auto', generative: 'auto', instructions: '', attachments: [], target_duration: null }

const TYPES = [['short', 'Short / Reel / TikTok', 'Vertical y corto: ritmo rápido, subtítulos grandes'],
  ['long', 'Video largo de YouTube', 'Horizontal: ritmo de explicación, cambios cada 15-30 s, b-roll cada 30-60 s']]
const STYLES = [['informativo', 'Informativo / noticia'], ['entretenimiento', 'Entretenimiento'], ['gaming', 'Gaming'],
  ['tutorial', 'Tutorial'], ['podcast', 'Podcast / charla'], ['vlog', 'Vlog'], ['viral', 'Viral / muy dinámico']]
const ROLES = [['auto', 'Que la IA decida'], ['chroma_bg', 'Fondo de la pantalla verde'], ['broll', 'Tomas de apoyo (b-roll)']]

const fmtDur = (s) => (s < 60 ? `${Math.round(s)} s` : `${Math.floor(s / 60)}:${String(Math.round(s % 60)).padStart(2, '0')} min`)

/** Duración final: la IA se queda con las mejores partes hasta llegar a ese tiempo (nunca alarga). */
function DurationPicker({ value, onChange, vt }) {
  const presets = vt === 'short' ? [30, 45, 60, 90] : [300, 480, 600, 900, 1200]
  const custom = value && !presets.includes(value)
  const [open, setOpen] = useState(custom)
  const [mins, setMins] = useState(custom ? +(value / 60).toFixed(2) : '')
  return (
    <div>
      <div className="seg seg-wrap">
        <button className={!value && !open ? 'on' : ''} onClick={() => { setOpen(false); onChange(null) }}>Todo lo útil</button>
        {presets.map((p) => (
          <button key={p} className={value === p && !open ? 'on' : ''} onClick={() => { setOpen(false); onChange(p) }}>{fmtDur(p)}</button>
        ))}
        <button className={open ? 'on' : ''} onClick={() => setOpen(true)}>Otra</button>
      </div>
      {open && (
        <div className="row mt-s">
          <input className="input sm" type="number" min="0.2" step="0.5" placeholder="Minutos" value={mins} style={{ width: 120 }}
            onChange={(e) => { setMins(e.target.value); const m = parseFloat(e.target.value); onChange(m > 0 ? Math.round(m * 60) : null) }} />
          <span className="muted small">minutos{value ? ` (${fmtDur(value)})` : ''}</span>
        </div>
      )}
      <div className="muted small">
        {value ? `Quita silencios y luego se queda con las partes más importantes (la IA califica cada fragmento, sin cortar frases) hasta ${fmtDur(value)}. Si tu video ya dura menos, no se alarga.`
          : 'Solo quita silencios y muletillas: dura lo que dure lo que dijiste.'}
      </div>
    </div>
  )
}

function Tri({ value, onChange, labels = ['Automático', 'Sí', 'No'] }) {
  return (
    <div className="seg">
      {['auto', 'on', 'off'].map((v, i) => <button key={v} className={value === v ? 'on' : ''} onClick={() => onChange(v)}>{labels[i]}</button>)}
    </div>
  )
}

export default function BriefForm({ value, onChange, format, excludeVideoId }) {
  const b = { ...EMPTY_BRIEF, ...(value || {}) }
  const set = (patch) => onChange({ ...b, ...patch })
  const [styles, setStyles] = useState([])
  const [videos, setVideos] = useState([])
  const [pick, setPick] = useState('')
  useEffect(() => {
    fetch('/api/studio/styles').then((r) => r.json()).then(setStyles).catch(() => {})
    api.library().then((d) => setVideos(d.items)).catch(() => {})
  }, [])
  const vt = b.video_type || (format === 'horizontal' ? 'long' : 'short')
  const byId = Object.fromEntries(videos.map((v) => [v.id, v]))
  const addAtt = () => {
    if (!pick) return
    if (!b.attachments.some((a) => a.video_id === +pick)) {
      const v = byId[+pick]
      const guess = /gameplay|juego|gta|partida|emulador/i.test(v?.title || '') ? 'chroma_bg' : 'auto'
      set({ attachments: [...b.attachments, { video_id: +pick, role: guess, note: '' }] })
    }
    setPick('')
  }
  return (
    <div className="brief">
      <label className="field-label">¿Qué vas a hacer?</label>
      <div className="brief-types">
        {TYPES.map(([k, l, h]) => (
          <button key={k} className={`brief-type ${vt === k ? 'on' : ''}`} onClick={() => set({ video_type: k })}>
            <b>{l}</b><span className="muted small">{h}</span>
          </button>
        ))}
      </div>

      <label className="field-label mt">Tipo de edición</label>
      <div className="chips">
        {STYLES.map(([k, l]) => (
          <button key={k} className={`chip ${(b.edit_style || (vt === 'long' ? 'informativo' : 'viral')) === k ? 'chip-on' : ''}`}
            onClick={() => set({ edit_style: k })}>{l}</button>
        ))}
      </div>

      <label className="field-label mt">Duración final</label>
      <DurationPicker value={b.target_duration || null} onChange={(v) => set({ target_duration: v })} vt={vt} />

      <label className="field-label mt">Estilo aprendido (de tus videos de referencia)</label>
      <select className="input full" value={b.learned_style_id || ''} onChange={(e) => set({ learned_style_id: e.target.value ? +e.target.value : null })}>
        <option value="">Ninguno: usar el tipo de edición</option>
        {styles.map((s) => <option key={s.id} value={s.id}>{s.name}{s.profile?.n_videos > 1 ? ` (${s.profile.n_videos} videos)` : ''}</option>)}
      </select>
      {b.learned_style_id && <div className="muted small">Se copian sus colores, letras, transiciones, tipos de sonido y ritmo. En videos largos se limita lo exagerado (efectos por minuto, zooms).</div>}

      <div className="brief-grid mt">
        <div>
          <label className="field-label">Subtítulos</label>
          <Tri value={b.captions} onChange={(v) => set({ captions: v })} />
          <div className="muted small">Automático: sí en shorts, no en videos largos.</div>
          {b.captions !== 'off' && (
            <select className="input full mt-s" value={b.caption_look} onChange={(e) => set({ caption_look: e.target.value })}>
              <option value="auto">Aspecto automático</option>
              <option value="clean">Limpio (frase en caja, para videos largos)</option>
              <option value="karaoke">Palabra resaltada (shorts)</option>
              <option value="simple">Frases simples</option>
            </select>
          )}
        </div>
        <div>
          <label className="field-label">Música de fondo</label>
          <Tri value={b.music} onChange={(v) => set({ music: v })} />
          <label className="field-label mt-s">Pantalla verde (chroma)</label>
          <Tri value={b.chroma} onChange={(v) => set({ chroma: v })} />
          <label className="field-label mt-s" title="Busca en internet imágenes, logos, capturas y clips reales de lo que dices, los verifica con visión y los guarda en tu índice para reutilizarlos">Recursos de internet (edición generativa)</label>
          <Tri value={b.generative || 'auto'} onChange={(v) => set({ generative: v })} />
          <div className="muted small">Automático: se activa si detecta fondo verde y adjuntaste un video para el fondo.</div>
        </div>
      </div>

      <label className="field-label mt">Videos de tu biblioteca para usar en la edición</label>
      <div className="row">
        <select className="input" value={pick} onChange={(e) => setPick(e.target.value)}>
          <option value="">Elige un video (gameplay, otra toma, material de apoyo)…</option>
          {videos.filter((v) => v.id !== excludeVideoId && !b.attachments.some((a) => a.video_id === v.id)).map((v) => <option key={v.id} value={v.id}>{v.title}</option>)}
        </select>
        <button className="btn" disabled={!pick} onClick={addAtt}>Adjuntar</button>
      </div>
      {b.attachments.map((a, i) => (
        <div key={a.video_id} className="brief-att">
          <span className="brief-att-title">{byId[a.video_id]?.title || `Video ${a.video_id}`}</span>
          <select className="input sm" value={a.role} onChange={(e) => { const n = [...b.attachments]; n[i] = { ...a, role: e.target.value }; set({ attachments: n }) }}>
            {ROLES.map(([k, l]) => <option key={k} value={k}>{l}</option>)}
          </select>
          <input className="input sm" placeholder="Nota (opcional): p. ej. usar la parte de la persecución" value={a.note}
            onChange={(e) => { const n = [...b.attachments]; n[i] = { ...a, note: e.target.value }; set({ attachments: n }) }} />
          <button className="icon-btn sm" title="Quitar" onClick={() => set({ attachments: b.attachments.filter((_, j) => j !== i) })}>✕</button>
        </div>
      ))}

      <label className="field-label mt">Instrucciones para la IA (opcional)</label>
      <textarea className="input full" rows={3} value={b.instructions} onChange={(e) => set({ instructions: e.target.value })}
        placeholder="Ej.: es un video serio, nada de memes; pon un texto con cada dato importante; corta la parte donde me trabo al minuto 3" />
    </div>
  )
}
