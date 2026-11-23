// Modelos por tarea: como un equipo de edición, cada modelo hace lo suyo.
import { useEffect, useState } from 'react'
import { api } from '../api.js'
import { useModels } from './ModelPicker.jsx'

const ROLES = [
  ['ollama_model', 'analysis', 'Analiza', 'Elige los mejores momentos del video o stream'],
  ['judge_model', 'judge', 'Juez', 'Revisa gancho, retención y remate de cada clip'],
  ['director_model', 'director', 'Editor (director)', 'Decide zooms, memes, textos y sonidos; atiende tus instrucciones en el Studio'],
  ['writer_model', 'writer', 'Escritor', 'Títulos, descripciones y guiones de videos desde un tema'],
  ['thumbnail_model', 'thumbnail', 'Miniaturas', 'Diseña las miniaturas (la de visión elige caras y califica cómo se ven)'],
]

export default function RolesPanel({ notify, compact = false }) {
  const m = useModels()
  const [roles, setRoles] = useState(null)
  const [judgeAuto, setJudgeAuto] = useState(null)
  const load = () => api.aiStatus().then((s) => { setRoles(s.roles || {}); setJudgeAuto(s.judge?.model) }).catch(() => {})
  useEffect(() => { load() }, [])
  if (!m || !roles) return null
  const models = m.models || []
  const save = async (key, value) => {
    try {
      await api.saveAiSettings({ [key]: value })
      await load()
      notify?.('Modelo guardado: se usa desde el próximo trabajo')
    } catch (e) { notify?.(e.message, 'error') }
  }
  const body = (
    <div className="roles">
      {ROLES.map(([key, role, label, help]) => {
        const val = role === 'analysis' ? roles.analysis : roles[role] || ''
        return (
          <label key={key} className="role-row" title={help}>
            <span className="role-name">{label}<small className="muted">{help}</small></span>
            <select className="input" value={val} disabled={!m.available} onChange={(e) => save(key, e.target.value)}>
              {role !== 'analysis' && (
                <option value="">{role === 'judge' ? `Automático (${judgeAuto || 'otro modelo'})` : `Igual que el que analiza (${roles.analysis})`}</option>
              )}
              {models.map((x) => <option key={x} value={x}>{x}</option>)}
            </select>
          </label>
        )
      })}
      {!m.available && <p className="muted small">Abre Ollama para elegir modelos.</p>}
    </div>
  )
  if (!compact) return <div className="panel"><h2>Modelos por tarea</h2>{body}</div>
  return (
    <details className="roles-pop">
      <summary className="btn sm">🧠 Modelos por tarea</summary>
      <div className="roles-pop-body">{body}</div>
    </details>
  )
}
