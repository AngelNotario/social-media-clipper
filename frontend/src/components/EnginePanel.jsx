import { useEffect, useRef, useState } from 'react'

// Motor de IA: Ollama (varios modelos) o llama.cpp (un modelo residente rápido con MTP y visión).
export default function EnginePanel({ notify }) {
  const [st, setSt] = useState(null)
  const [busy, setBusy] = useState('')
  const [test, setTest] = useState(null)
  const [cfg, setCfg] = useState(null)
  const timer = useRef(null)

  const load = async () => {
    try {
      const s = await fetch('/api/ai/llamacpp').then((r) => r.json())
      setSt(s)
      setCfg((c) => c || s.config)
      return s
    } catch (e) { notify(e.message, 'error') }
    return null
  }
  useEffect(() => {
    load()
    timer.current = setInterval(load, 4000) // muestra cuando termina de cargar el modelo
    return () => clearInterval(timer.current)
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  const call = async (what, url, opts = {}) => {
    setBusy(what)
    try {
      const r = await fetch(url, { method: 'POST', ...opts })
      const j = await r.json()
      if (!r.ok) throw new Error(j.detail || 'Falló')
      return j
    } catch (e) { notify(e.message, 'error') } finally { setBusy('') }
    return null
  }
  const setBackend = async (b) => {
    setBusy('backend')
    try {
      await fetch('/api/ai/settings', { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ llm_backend: b }) })
      if (b === 'llamacpp' && st?.health === 'down') await call('start', '/api/ai/llamacpp/start')
      notify(b === 'llamacpp' ? 'Motor: llama.cpp (Qwen3.6 35B con MTP)' : 'Motor: Ollama')
      await load()
    } finally { setBusy('') }
  }
  const saveCfg = async () => {
    setBusy('cfg')
    try {
      const r = await fetch('/api/ai/llamacpp', { method: 'PUT', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ...cfg, llamacpp_ctx: Number(cfg.llamacpp_ctx), llamacpp_autostart: cfg.llamacpp_autostart === '1' || cfg.llamacpp_autostart === true }) })
      setSt(await r.json())
      notify('Configuración de llama.cpp guardada (reinicia el servidor para aplicarla)')
    } finally { setBusy('') }
  }

  if (!st) return null
  const isL = st.backend === 'llamacpp'
  const hl = { ok: '● Listo', loading: '◌ Cargando el modelo…', down: '○ Apagado' }[st.health]
  const files = st.files || {}
  return (
    <section className="panel engine-panel">
      <div className="panel-head"><h2>Motor de IA</h2></div>
      <div className="seg mb">
        <button className={!isL ? 'on' : ''} disabled={!!busy} onClick={() => setBackend('ollama')}>Ollama (varios modelos)</button>
        <button className={isL ? 'on' : ''} disabled={!!busy} onClick={() => setBackend('llamacpp')}>llama.cpp · Qwen3.6 35B + MTP</button>
      </div>
      <p className="muted small">
        Con llama.cpp un solo modelo (Qwen3.6-35B-A3B Q4 con MTP y visión) queda cargado y hace todas las tareas: analizar,
        dirigir, escribir, juzgar, ver imágenes y miniaturas. Reparte el modelo entre tus 12 GB de VRAM y la RAM.
        «Modelos por tarea» no aplica en este modo.
      </p>
      <div className="engine-status">
        <span className={`badge ${st.health === 'ok' ? 'badge-ok' : st.health === 'loading' ? 'badge-warn' : ''}`}>{hl}</span>
        {st.started_by_app && <span className="muted small">abierto por la app</span>}
        {st.error && <span className="error-text small">{st.error}</span>}
        <div className="row">
          {st.health === 'down'
            ? <button className="btn sm btn-primary" disabled={!!busy} onClick={async () => { await call('start', '/api/ai/llamacpp/start'); load() }}>{busy === 'start' ? 'Abriendo…' : 'Iniciar servidor'}</button>
            : st.started_by_app && <button className="btn sm" disabled={!!busy} onClick={async () => { await call('stop', '/api/ai/llamacpp/stop'); load() }}>Detener</button>}
          <button className="btn sm" disabled={st.health !== 'ok' || !!busy} onClick={async () => setTest(await call('test', '/api/ai/llamacpp/test'))}>
            {busy === 'test' ? 'Probando…' : '⚡ Probar velocidad'}</button>
        </div>
      </div>
      {test && (
        <div className="note small">
          <b>{test.tokens_per_s} tokens/s</b> al escribir · {test.prompt_per_s} tokens/s al leer · {test.seconds} s en total
          {test.draft_total ? ` · MTP acertó ${test.draft_accepted}/${test.draft_total} borradores` : ''}
          {test.vision && (test.vision.ok ? ` · visión OK en ${test.vision.seconds} s (${test.vision.answer?.fondo} / ${test.vision.answer?.texto})` : ` · visión: ${test.vision.error}`)}
          <div className="muted" style={{ whiteSpace: 'pre-wrap', marginTop: 4 }}>{test.text}</div>
        </div>
      )}
      <details className="mt">
        <summary className="muted small">Archivos y opciones avanzadas</summary>
        {cfg && (
          <div className="engine-cfg">
            {[['llamacpp_exe', 'llama-server.exe'], ['llamacpp_model', 'Modelo (.gguf)'], ['llamacpp_draft', 'MTP (velocidad)'], ['llamacpp_mmproj', 'Visión (mmproj)']].map(([k, l]) => (
              <label key={k} className="insp-field">{l} {files[k] ? <span className="txt-ok">✓</span> : <span className="error-text">✗ no existe</span>}
                <input className="input" value={cfg[k] || ''} onChange={(e) => setCfg({ ...cfg, [k]: e.target.value })} />
              </label>
            ))}
            <div className="insp-row">
              <label className="insp-field">Contexto (tokens)
                <select className="input" value={cfg.llamacpp_ctx} onChange={(e) => setCfg({ ...cfg, llamacpp_ctx: e.target.value })}>
                  {['16384', '32768', '65536', '131072', '262144'].map((v) => <option key={v} value={v}>{Number(v) / 1024}K</option>)}
                </select>
              </label>
              <label className="insp-field">Dirección
                <input className="input" value={cfg.llamacpp_url} onChange={(e) => setCfg({ ...cfg, llamacpp_url: e.target.value })} />
              </label>
            </div>
            <label className="check"><input type="checkbox" checked={cfg.llamacpp_autostart === '1' || cfg.llamacpp_autostart === true}
              onChange={(e) => setCfg({ ...cfg, llamacpp_autostart: e.target.checked ? '1' : '0' })} /> Abrir el servidor al iniciar la app</label>
            <label className="insp-field">Opciones extra de llama-server
              <input className="input" placeholder="ej. --n-cpu-moe 20" value={cfg.llamacpp_extra || ''} onChange={(e) => setCfg({ ...cfg, llamacpp_extra: e.target.value })} />
            </label>
            <button className="btn sm" disabled={!!busy} onClick={saveCfg}>Guardar opciones</button>
            <code className="block small">{st.command}</code>
            {st.log && <details><summary className="muted small">Registro del servidor</summary><pre className="engine-log">{st.log}</pre></details>}
          </div>
        )}
      </details>
    </section>
  )
}
