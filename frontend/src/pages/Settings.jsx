import { useEffect, useState } from 'react'
import Icon from '../components/Icon.jsx'
import { api } from '../api.js'
import RolesPanel from '../components/RolesPanel.jsx'
import EnginePanel from '../components/EnginePanel.jsx'
import VocabPanel from '../components/VocabPanel.jsx'

export default function Settings({ notify }) {
  const [folders, setFolders] = useState([])
  const [dataDir, setDataDir] = useState('')
  const [input, setInput] = useState('')
  const [saving, setSaving] = useState(false)

  const apply = (d) => { setFolders(d.authorized_folders); setDataDir(d.data_dir) }
  useEffect(() => { api.settings().then(apply).catch((e) => notify(e.message, 'error')) }, [notify])

  const save = async (list) => {
    setSaving(true)
    try {
      apply(await api.saveSettings(list))
      notify('Carpetas actualizadas')
      return true
    } catch (e) {
      notify(e.message, 'error')
      return false
    } finally { setSaving(false) }
  }

  const add = async () => {
    if (!input.trim()) return
    if (await save([...folders.map((f) => f.path), input.trim()])) setInput('')
  }
  const remove = (p) => save(folders.map((f) => f.path).filter((x) => x !== p))

  return (
    <div className="page">
      <header className="page-head">
        <div>
          <h1>Configuración</h1>
          <p className="muted">La app solo puede ver y leer archivos dentro de estas carpetas.</p>
        </div>
      </header>
      <EnginePanel notify={notify} />
      <RolesPanel notify={notify} />
      <VocabPanel notify={notify} />

      <section className="panel">
        <div className="panel-head"><h2>Carpetas autorizadas</h2></div>
        <ul className="filelist">
          {folders.map((f) => (
            <li key={f.path} className={`file-row ${f.exists ? '' : 'missing'}`}>
              <Icon name="folder" />
              <span className="fname">{f.path}</span>
              {!f.exists && <span className="badge badge-warn">No existe</span>}
              <button className="icon-btn" title="Quitar" onClick={() => remove(f.path)} disabled={saving}><Icon name="trash" /></button>
            </li>
          ))}
          {folders.length === 0 && <li className="empty">Sin carpetas autorizadas.</li>}
        </ul>
        <div className="row add-row">
          <input className="input" placeholder="Pega una ruta, ej. C:\Users\TuUsuario\Videos"
            value={input} onChange={(e) => setInput(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && add()} />
          <button className="btn btn-primary" onClick={add} disabled={saving || !input.trim()}><Icon name="plus" /> Agregar</button>
        </div>
        <p className="muted small">Tip: en el Explorador de Windows, clic derecho sobre una carpeta → "Copiar como ruta de acceso".</p>
      </section>

      <section className="panel">
        <div className="panel-head"><h2>Almacenamiento</h2></div>
        <p className="muted small">Base de datos, miniaturas y copias importadas se guardan en:</p>
        <code className="block">{dataDir}</code>
      </section>
    </div>
  )
}
