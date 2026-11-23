import { useState } from 'react'

/** 👍/👎 + nota. Tu opinión se guarda y la IA la lee en las próximas ediciones. */
export default function Feedback({ kind, refId, context, notify, compact }) {
  const [rating, setRating] = useState(0)
  const [note, setNote] = useState('')
  const [sent, setSent] = useState(false)
  const send = async (r = rating) => {
    try {
      const res = await fetch('/api/feedback', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ kind, ref_id: refId ?? null, rating: r, note: note.trim() || null, context }),
      })
      if (!res.ok) throw new Error('No se pudo guardar tu opinión')
      setSent(true); notify?.('Gracias: la IA lo tendrá en cuenta en las próximas ediciones.')
    } catch (e) { notify?.(e.message, 'error') }
  }
  if (sent) return <div className="feedback sent small">✓ Opinión guardada. <button className="link-btn" onClick={() => { setSent(false); setNote(''); setRating(0) }}>Agregar otra</button></div>
  return (
    <div className={`feedback ${compact ? 'compact' : ''}`}>
      <span className="small muted">¿Qué te pareció la edición?</span>
      <button className={`fb-btn ${rating === 1 ? 'on up' : ''}`} onClick={() => setRating(1)} title="Me gusta">👍</button>
      <button className={`fb-btn ${rating === -1 ? 'on down' : ''}`} onClick={() => setRating(-1)} title="No me gusta">👎</button>
      {rating !== 0 && (<>
        <input className="input fb-note" value={note} onChange={(e) => setNote(e.target.value)} maxLength={600}
          placeholder={rating > 0 ? 'Qué te gustó (ej. los memes, el ritmo, el gancho)…' : 'Qué cambiarías (ej. más memes, cortes más rápidos, sin subtítulos)…'}
          onKeyDown={(e) => { if (e.key === 'Enter') send() }} />
        <button className="btn sm btn-primary" onClick={() => send()}>Enviar</button>
      </>)}
    </div>
  )
}
