// Segunda opinión sobre el clip: juez de gancho y retención (reglas medibles + IA con rúbrica).
import { useState } from 'react'
import ModelPicker from './ModelPicker.jsx'
import Icon from './Icon.jsx'
import { fmtTime } from '../api.js'

const VERDICT = { publicar: ['Listo para publicar', 'jv-ok'], mejorar: ['Sirve con ajustes', 'jv-warn'], descartar: ['Mejor descartarlo', 'jv-bad'] }
const AXES = [['hook', 'Gancho (3 s)'], ['retention', 'Retención'], ['payoff', 'Remate'], ['standalone', 'Se entiende solo']]

export default function JudgePanel({ clip, notify, onChanged, onApplyTrim, onUseHook }) {
  const j = clip.judge
  const [model, setModel] = useState('')
  const [busy, setBusy] = useState(false)

  const rejudge = async () => {
    setBusy(true)
    try {
      const r = await fetch(`/api/clips/${clip.id}/judge`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ llm_model: model || null }),
      })
      const d = await r.json()
      if (!r.ok) throw new Error(d.detail || 'No se pudo evaluar')
      onChanged?.(d)
      notify('El juez revisó el clip')
    } catch (e) { notify(e.message, 'error') } finally { setBusy(false) }
  }

  const [vlabel, vcls] = VERDICT[j?.verdict] || (j ? ['Solo medición (sin IA)', 'jv-warn'] : [null, null])
  return (
    <div className="panel judge">
      <div className="panel-head">
        <h2>Juez de retención</h2>
        {j && <b className="score-big">{Math.round(j.final)}</b>}
      </div>
      {!j && <p className="muted small">Este clip aún no tiene segunda opinión. El juez mide cuándo arranca la voz, el aire muerto y si cierra la idea, y una IA distinta revisa gancho, retención y remate.</p>}
      {j && (<>
        <div className="row wrap">
          <span className={`jv ${vcls}`}>{vlabel}</span>
          {j.model && <span className="muted small">por {j.model}</span>}
        </div>
        {j.model && AXES.map(([k, l]) => (
          <div key={k} className="score-row">
            <span>{l}</span>
            <div className="bar"><div className="bar-fill" style={{ width: `${(j[k] || 0) * 10}%` }} /></div>
            <span className="mono small">{j[k]}/10</span>
          </div>
        ))}
        {j.taste != null && (
          <div className="score-row">
            <span>Tu gusto</span>
            <div className="bar"><div className="bar-fill alt" style={{ width: `${j.taste}%` }} /></div>
            <span className="mono small">{Math.round(j.taste)}</span>
          </div>
        )}
        <div className="score-row">
          <span>Medición</span>
          <div className="bar"><div className="bar-fill alt" style={{ width: `${j.rules}%` }} /></div>
          <span className="mono small">{Math.round(j.rules)}</span>
        </div>
        {j.trimmed && (j.trimmed.start || j.trimmed.end) && (
          <p className="small judge-note">✂ Ya recortado: {j.trimmed.start ? `${j.trimmed.start} s del inicio` : ''}{j.trimmed.start && j.trimmed.end ? ' y ' : ''}{j.trimmed.end ? `${j.trimmed.end} s del final` : ''} para empezar en el gancho y cerrar en el remate.</p>
        )}
        {j.suggest && (
          <div className="judge-note">
            <span className="small">Recorte sugerido: {fmtTime(j.suggest.start)} → {fmtTime(j.suggest.end)}</span>
            <button className="btn sm" onClick={() => onApplyTrim(j.suggest.start, j.suggest.end)}>Aplicar</button>
          </div>
        )}
        {j.hook_text && (
          <div className="judge-note">
            <span className="small">Texto de gancho: <b>“{j.hook_text}”</b></span>
            <button className="btn sm" onClick={() => onUseHook(j.hook_text)}>Usar en pantalla</button>
          </div>
        )}
        {(j.reasons?.length > 0 || j.rule_notes?.length > 0) && (
          <ul className="judge-list small">
            {(j.reasons || []).map((r, i) => <li key={`r${i}`}>{r}</li>)}
            {(j.rule_notes || []).map((r, i) => <li key={`n${i}`} className="muted">{r}</li>)}
          </ul>
        )}
        {j.fix_tip && <p className="small"><b>Mejora clave:</b> {j.fix_tip}</p>}
        {j.error && <p className="small muted">La IA no respondió: {j.error}</p>}
      </>)}
      <div className="judge-actions">
        <ModelPicker value={model} onChange={setModel} label="Modelo del juez" compact defaultLabel="Automático (otro modelo distinto)" />
        <button className="btn" onClick={rejudge} disabled={busy}><Icon name="refresh" /> {busy ? 'Revisando…' : j ? 'Re-evaluar' : 'Evaluar'}</button>
      </div>
      <p className="muted small">Evalúa con los tiempos guardados. Opinión editorial, no predicción de viralidad.</p>
    </div>
  )
}
