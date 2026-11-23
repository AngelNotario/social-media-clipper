// Perfil de gusto: lo que la IA aprendió de los clips que apruebas, descartas y calificas.
import { useCallback, useEffect, useState } from 'react'

const CRIT = { hook: 'Gancho', contexto: 'Se entiende solo', emocion: 'Emoción', valor: 'Valor', narrativa: 'Narrativa', retencion: 'Retención' }
const MIX = { seleccion: 'IA que elige', juez: 'Juez de retención', reglas: 'Medición' }

export default function TastePanel({ notify }) {
  const [t, setT] = useState(null)
  const load = useCallback(() => { fetch('/api/knowledge/taste').then((r) => r.json()).then(setT).catch(() => {}) }, [])
  useEffect(() => { load() }, [load])
  if (!t) return null
  const reset = async () => {
    if (!window.confirm('¿Borrar todo lo que la IA aprendió de tus aprobaciones y descartes? Tus clips no se tocan.')) return
    const r = await fetch('/api/knowledge/taste/reset', { method: 'POST' })
    if (r.ok) { setT(await r.json()); notify('Perfil de gusto reiniciado') }
  }
  const pct = (x) => `${Math.round(x * 100)} %`
  const need = Math.max(0, t.min.pos - t.n_pos) + Math.max(0, t.min.neg - t.n_neg)
  return (
    <div className="panel taste-panel">
      <h2>Tu perfil de gusto</h2>
      <p className="muted small">Cada clip que apruebas, descartas o calificas con 👍/👎 queda como ejemplo. La IA lo usa para elegir
        momentos, juzgarlos y decidir cuántos efectos poner. Los ejemplos se conservan aunque vuelvas a analizar el video.</p>
      <div className="taste-counts">
        <span className="jv jv-ok">{t.n_pos} aprobado{t.n_pos === 1 ? '' : 's'}</span>
        <span className="jv jv-bad">{t.n_neg} descartado{t.n_neg === 1 ? '' : 's'}</span>
        {t.ready
          ? <span className="small">Ajustando pesos y afinidad con tu gusto</span>
          : <span className="small muted">Faltan {need} para ajustar pesos (mín. {t.min.pos} aprobados y {t.min.neg} descartados). Los ejemplos ya se usan.</span>}
      </div>

      {t.notes?.length > 0 && (<>
        <h3 className="mt">Lo que notó</h3>
        <ul className="judge-list small">{t.notes.map((n, i) => <li key={i}>{n}</li>)}</ul>
      </>)}

      {t.ready && (<>
        <h3 className="mt">Peso de cada criterio al elegir</h3>
        {Object.keys(CRIT).map((k) => (
          <div key={k} className="score-row">
            <span>{CRIT[k]}</span>
            <div className="bar"><div className="bar-fill" style={{ width: `${Math.min(100, t.criteria[k] * 250)}%` }} /></div>
            <span className="mono small">{pct(t.criteria[k])}{Math.abs(t.criteria[k] - t.default_criteria[k]) >= 0.01 ? (t.criteria[k] > t.default_criteria[k] ? ' ▲' : ' ▼') : ''}</span>
          </div>
        ))}
        <h3 className="mt">Cuánto pesa cada opinión en la nota final</h3>
        {Object.keys(MIX).map((k) => (
          <div key={k} className="score-row">
            <span>{MIX[k]}</span>
            <div className="bar"><div className="bar-fill alt" style={{ width: `${t.mix[k] * 100}%` }} /></div>
            <span className="mono small">{pct(t.mix[k])}{t.accuracy?.[k] != null ? ` · acierta ${pct(t.accuracy[k])}` : ''}</span>
          </div>
        ))}
        {t.accuracy?.gusto != null && <p className="small muted">La afinidad con tu gusto ordena bien {pct(t.accuracy.gusto)} de los pares aprobado/descartado (medido sobre tus propios ejemplos).</p>}
      </>)}

      <div className="taste-facts small">
        {t.duration && <span>Duración que apruebas: <b>{Math.round(t.duration[0])}–{Math.round(t.duration[1])} s</b></span>}
        <span>Efectos del director: <b>{t.fx_density === 1 ? 'normal' : `${t.fx_density > 1 ? '+' : ''}${Math.round((t.fx_density - 1) * 100)} %`}</b></span>
      </div>

      {(t.examples.pos.length > 0 || t.examples.neg.length > 0) && (<>
        <h3 className="mt">Ejemplos que ve la IA</h3>
        <ul className="taste-ex small">
          {t.examples.pos.slice(0, 3).map((e, i) => <li key={`p${i}`}><span>👍</span> <b>{e.title}</b>{e.note ? ` — ${e.note}` : ''}</li>)}
          {t.examples.neg.slice(0, 3).map((e, i) => <li key={`n${i}`}><span>👎</span> <b>{e.title}</b>{e.note ? ` — ${e.note}` : ''}</li>)}
        </ul>
      </>)}
      {(t.n_pos + t.n_neg) > 0 && <button className="btn sm mt" onClick={reset}>Reiniciar aprendizaje</button>}
    </div>
  )
}
