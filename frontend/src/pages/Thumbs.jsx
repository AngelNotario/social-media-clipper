import { useCallback, useEffect, useMemo, useState } from 'react'
import Icon from '../components/Icon.jsx'
import JobProgress from '../components/JobProgress.jsx'
import RolesPanel from '../components/RolesPanel.jsx'
import { useJob } from '../hooks.js'

const j = (url, opt) => fetch(url, opt).then(async (r) => { const d = await r.json().catch(() => ({})); if (!r.ok) throw new Error(d.detail || 'Error'); return d })

async function uploadImage(file) {
  const fd = new FormData(); fd.append('file', file)
  return j('/api/thumbs/upload', { method: 'POST', body: fd })
}

function RefList({ items, setItems, placeholder, notify, label }) {
  const [link, setLink] = useState('')
  const add = () => { const v = link.trim(); if (/^https?:\/\//.test(v)) { setItems([...items, { ref: v, url: v }]); setLink('') } }
  return (
    <div>
      <label className="field-label">{label}</label>
      <div className="row">
        <input className="input" value={link} onChange={(e) => setLink(e.target.value)} placeholder={placeholder} onKeyDown={(e) => e.key === 'Enter' && add()} />
        <button className="btn" onClick={add} disabled={!link.trim()}>Agregar</button>
        <label className="btn"><Icon name="upload" size={14} /> Subir
          <input type="file" accept="image/*" multiple hidden onChange={async (e) => {
            const out = []
            for (const f of e.target.files) { try { out.push(await uploadImage(f)) } catch (err) { notify(err.message, 'error') } }
            setItems([...items, ...out])
          }} />
        </label>
      </div>
      {items.length > 0 && (
        <div className="th-refs">
          {items.map((it, i) => {
            const yt = it.ref.match(/(?:v=|youtu\.be\/|shorts\/)([A-Za-z0-9_-]{11})/)
            const src = yt ? `https://i.ytimg.com/vi/${yt[1]}/mqdefault.jpg` : it.url
            return (
              <div key={i} className="th-ref">
                <img src={src} alt="" onError={(e) => { e.target.style.opacity = 0.3 }} />
                <button className="icon-btn sm" onClick={() => setItems(items.filter((_, k) => k !== i))}>✕</button>
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}

function ThumbCard({ t, onChanged, notify }) {
  const [edit, setEdit] = useState(false)
  const [f, setF] = useState({ text: t.concept.text, highlight_word: t.concept.highlight_word, accent_color: t.concept.accent_color || '#FFD400', text_color: t.concept.text_color || '#FFFFFF', layout: t.concept.layout || 'left' })
  const [busy, setBusy] = useState(false)
  const save = async () => {
    setBusy(true)
    try { await j(`/api/thumbs/${t.id}`, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(f) }); onChanged(); setEdit(false) } catch (e) { notify(e.message, 'error') }
    setBusy(false)
  }
  const d = t.detail || {}
  return (
    <div className={`th-card ${t.chosen ? 'chosen' : ''}`}>
      <img src={`${t.url}?v=${encodeURIComponent(t.path)}`} alt="" />
      <div className="th-meta">
        {t.score != null && <span className={`badge ${t.score >= 7 ? 'badge-ok' : t.score >= 5.5 ? 'badge-warn' : 'badge-err'}`}>{t.score.toFixed(1)}/10</span>}
        {t.chosen ? <span className="badge badge-ok">Elegida para publicar</span> : null}
      </div>
      {t.critique && <div className="muted small">Juez: {t.critique}</div>}
      {d.legibilidad != null && <div className="muted small">Legible {d.legibilidad} · Curiosidad {d.curiosidad} · Emoción {d.emocion} · Fiel {d.fiel_al_tema}</div>}
      {t.concept.idea && <div className="small">💡 {t.concept.idea}</div>}
      {edit && (
        <div className="th-edit">
          <input className="input full" value={f.text} onChange={(e) => setF({ ...f, text: e.target.value })} placeholder="Texto (2-4 palabras)" />
          <div className="row">
            <input className="input" value={f.highlight_word} onChange={(e) => setF({ ...f, highlight_word: e.target.value })} placeholder="Palabra resaltada" />
            <input type="color" className="input" value={f.accent_color} onChange={(e) => setF({ ...f, accent_color: e.target.value })} title="Color de la palabra resaltada" />
            <input type="color" className="input" value={f.text_color} onChange={(e) => setF({ ...f, text_color: e.target.value })} title="Color del texto" />
            <select className="input" value={f.layout} onChange={(e) => setF({ ...f, layout: e.target.value })}>
              <option value="left">Cara a la izquierda</option><option value="right">Cara a la derecha</option><option value="center">Cara al centro</option>
            </select>
          </div>
          <button className="btn sm btn-primary" disabled={busy} onClick={save}>{busy ? 'Aplicando…' : 'Aplicar'}</button>
        </div>
      )}
      <div className="row mt">
        {!t.chosen && <button className="btn sm btn-primary" onClick={async () => { await j(`/api/thumbs/${t.id}/choose`, { method: 'POST' }); onChanged(); notify('Se usará al publicar en YouTube') }}>Usar al publicar</button>}
        <button className="btn sm" onClick={() => setEdit(!edit)}>Editar texto</button>
        <a className="btn sm" href={`${t.url}?download=true`}>Descargar</a>
        <button className="icon-btn sm" title="Borrar" onClick={async () => { await j(`/api/thumbs/${t.id}`, { method: 'DELETE' }); onChanged() }}><Icon name="trash" size={14} /></button>
      </div>
    </div>
  )
}

export default function Thumbs({ notify, hash }) {
  const [sources, setSources] = useState([])
  const [src, setSrc] = useState('')
  const [instructions, setInstructions] = useState('')
  const [text, setText] = useState('')
  const [refs, setRefs] = useState([])
  const [assets, setAssets] = useState([])
  const [useWeb, setUseWeb] = useState(true)
  const [n, setN] = useState(3)
  const [thumbs, setThumbs] = useState([])
  const [jobId, setJobId] = useState(null)

  useEffect(() => {
    j('/api/thumbs/sources').then((s) => {
      setSources(s)
      const m = hash.match(/kind=(\w+)&id=(\d+)/)
      if (m) setSrc(`${m[1]}:${m[2]}`)
      else if (s.length) setSrc((x) => x || `${s[0].kind}:${s[0].id}`)
    }).catch((e) => notify(e.message, 'error'))
  }, [hash, notify])
  const [kind, id] = src ? src.split(':') : [null, null]
  const load = useCallback(() => { if (kind) j(`/api/thumbs?kind=${kind}&id=${id}`).then(setThumbs).catch(() => {}) }, [kind, id])
  useEffect(() => { load() }, [load])
  const job = useJob(jobId, (jb) => {
    if (jb.status === 'done') notify(`Listas ${jb.result.ids.length} miniaturas${jb.result.best ? ` · la mejor: ${jb.result.best.toFixed(1)}/10` : ''}`)
    else if (jb.status === 'error') notify(jb.error, 'error')
    load()
  })
  const running = job && ['queued', 'running'].includes(job.status)
  const groups = useMemo(() => sources.reduce((acc, s) => ((acc[s.group] = acc[s.group] || []).push(s), acc), {}), [sources])
  const batches = useMemo(() => thumbs.reduce((acc, t) => ((acc[t.batch] = acc[t.batch] || []).push(t), acc), {}), [thumbs])

  const generate = async () => {
    try {
      const r = await j('/api/thumbs/generate', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({
        source_kind: kind, source_id: Number(id), instructions, text, n, use_web: useWeb,
        references: refs.map((r) => r.ref), assets: assets.map((a) => a.ref) }) })
      setJobId(r.id)
    } catch (e) { notify(e.message, 'error') }
  }

  return (
    <div className="page">
      <header className="page-head">
        <div>
          <h1>Miniaturas</h1>
          <p className="muted">Una IA especializada diseña miniaturas virales de tu video: busca en internet qué miniaturas del tema funcionan hoy y recursos reales (logos, portadas), elige tus mejores caras, te recorta y un juez con visión las califica como se ven en el celular.</p>
        </div>
      </header>
      <div className="th-layout">
        <div className="panel">
          <label className="field-label">Video</label>
          <select className="input full" value={src} onChange={(e) => setSrc(e.target.value)}>
            {Object.entries(groups).map(([g, list]) => (
              <optgroup key={g} label={g}>{list.map((s) => <option key={`${s.kind}:${s.id}`} value={`${s.kind}:${s.id}`}>{s.title}</option>)}</optgroup>
            ))}
          </select>
          <label className="field-label mt">Instrucciones para el diseñador</label>
          <textarea className="input full" rows={3} value={instructions} onChange={(e) => setInstructions(e.target.value)}
            placeholder="Ej.: cara de enojo, logo de Eneba tachado, colores rojo y amarillo, que se entienda que me quedé sin GTA 6" />
          <label className="field-label mt">Texto de la miniatura (opcional)</label>
          <input className="input full" value={text} onChange={(e) => setText(e.target.value)} placeholder="Si lo dejas vacío, la IA propone 2-4 palabras" />
          <div className="mt"><RefList label="Ejemplos de miniaturas que te gustan" items={refs} setItems={setRefs} notify={notify} placeholder="Link de YouTube o URL de imagen" /></div>
          <div className="mt"><RefList label="Tus recursos (logos, fotos, capturas) para usar" items={assets} setItems={setAssets} notify={notify} placeholder="URL de imagen" /></div>
          <label className="check mt"><input type="checkbox" checked={useWeb} onChange={(e) => setUseWeb(e.target.checked)} /> Investigar en internet (miniaturas en tendencia del tema, recursos y datos actuales)</label>
          <div className="row mt">
            <label className="field-label">Variantes</label>
            <div className="seg">{[2, 3, 4, 6].map((x) => <button key={x} className={n === x ? 'on' : ''} onClick={() => setN(x)}>{x}</button>)}</div>
          </div>
          <button className="btn btn-primary full mt" disabled={!src || running} onClick={generate}><Icon name="spark" /> {running ? 'Diseñando…' : 'Crear miniaturas'}</button>
          {job && job.status !== 'done' && <JobProgress job={job} />}
          <details className="mt"><summary className="muted small">Modelos que usa</summary><RolesPanel compact notify={notify} /></details>
        </div>
        <div>
          {Object.keys(batches).length === 0 && <div className="panel muted">Aún no hay miniaturas para este video.</div>}
          {Object.entries(batches).map(([b, list]) => (
            <div key={b} className="panel">
              <div className="muted small">{list[0].created_at?.slice(0, 16)} · {list[0].info?.tema}</div>
              {list[0].info?.trend?.length > 0 && <div className="muted small">Referencias de hoy: {list[0].info.trend.map((t) => t.title).join(' · ')}</div>}
              {list[0].web_assets?.length > 0 && <div className="muted small">Recursos de internet: {list[0].web_assets.map((a, i) => <a key={i} href={a.page} target="_blank" rel="noreferrer">{a.source || 'fuente'} </a>)} (revisa que puedas usarlos)</div>}
              <div className="th-grid">{list.sort((a, c) => (c.score || 0) - (a.score || 0)).map((t) => <ThumbCard key={t.id} t={t} onChanged={load} notify={notify} />)}</div>
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}
