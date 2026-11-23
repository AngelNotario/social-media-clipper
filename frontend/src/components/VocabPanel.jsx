import { useEffect, useState } from 'react'
import { api } from '../api.js'

/** Diccionario de nombres: marcas, juegos y apodos que Whisper debe escribir bien. */
export default function VocabPanel({ notify }) {
  const [text, setText] = useState('')
  const [fixes, setFixes] = useState({})
  const [saving, setSaving] = useState(false)

  const apply = (s) => { setText((s.vocabulary || []).join(', ')); setFixes(s.vocab_fixes || {}) }
  useEffect(() => { api.aiStatus().then(apply).catch(() => {}) }, [])

  const save = async () => {
    setSaving(true)
    try {
      apply(await api.saveAiSettings({ vocabulary: text }))
      notify('Diccionario guardado: se aplica en la próxima auto-edición y en los clips')
    } catch (e) {
      notify(e.message, 'error')
    } finally { setSaving(false) }
  }

  const learned = Object.entries(fixes)
  return (
    <section className="panel">
      <div className="panel-head"><h2>Diccionario de nombres</h2></div>
      <p className="muted small">
        Marcas, juegos, tiendas y apodos que Whisper debe escribir bien, separados por coma. Se usan al transcribir y
        corrigen las transcripciones que ya tienes (subtítulos, tema del video, búsqueda de recursos y textos).
      </p>
      <textarea className="input" rows={3} placeholder="Eneba, GTA 6, Xbox Series S, Remote Play"
        value={text} onChange={(e) => setText(e.target.value)} />
      <div className="row" style={{ marginTop: 8 }}>
        <button className="btn btn-primary" onClick={save} disabled={saving}>{saving ? 'Guardando…' : 'Guardar diccionario'}</button>
      </div>
      {learned.length > 0 && (
        <p className="muted small" style={{ marginTop: 10 }}>
          Aprendido de tus correcciones de subtítulos: {learned.slice(-12).map(([k, v]) => `«${k}» → «${v}»`).join(' · ')}
        </p>
      )}
    </section>
  )
}
