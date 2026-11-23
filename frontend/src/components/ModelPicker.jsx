import { useEffect, useState } from 'react'

let cache = null
export function useModels() {
  const [data, setData] = useState(cache)
  useEffect(() => {
    fetch('/api/ai/models').then((r) => r.json()).then((d) => { cache = d; setData(d) }).catch(() => {})
  }, [])
  return data
}

const KEY = 'smc_llm_model'
export function useModelChoice() {
  const [model, setModel] = useState(() => localStorage.getItem(KEY) || '')
  const set = (m) => { setModel(m); try { localStorage.setItem(KEY, m) } catch { /* sin almacenamiento */ } }
  return [model, set]
}

/** Menú con los modelos de IA instalados en Ollama. Valor '' = el de Configuración. */
export default function ModelPicker({ value, onChange, label = 'Modelo de IA', compact, defaultLabel }) {
  const d = useModels()
  if (!d) return null
  const models = d.models || []
  const v = value && !models.includes(value) ? '' : value
  return (
    <label className={`model-picker ${compact ? 'compact' : ''}`} title="Modelos instalados en Ollama. Para agregar otro: ollama pull <modelo>">
      {!compact && <span className="field-label">{label}</span>}
      <select className="input" value={v} onChange={(e) => onChange(e.target.value)} disabled={!d.available}>
        <option value="">{d.available ? (defaultLabel || `Predeterminado (${d.default})`) : 'Ollama no está abierto'}</option>
        {models.filter((m) => defaultLabel || m !== d.default).map((m) => <option key={m} value={m}>{m}</option>)}
      </select>
    </label>
  )
}
