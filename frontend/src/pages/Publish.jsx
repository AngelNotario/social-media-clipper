import { useCallback, useEffect, useState } from 'react'
import Icon from '../components/Icon.jsx'
import PublishComposer from '../components/PublishComposer.jsx'
import { fmtDuration, pub } from '../api.js'

const NAMES = { youtube: 'YouTube', tiktok: 'TikTok', instagram: 'Instagram' }
const STATUS = { scheduled: ['Programada', 'badge-warn'], queued: ['En cola', 'badge-warn'], publishing: ['Subiendo', 'badge-warn'],
  published: ['Publicada', 'badge-ok'], failed: ['Error', 'badge-err'], cancelled: ['Cancelada', ''] }

const FIELDS = {
  youtube: [['client_id', 'ID de cliente'], ['client_secret', 'Secreto del cliente']],
  tiktok: [['client_key', 'Client key'], ['client_secret', 'Client secret']],
  instagram: [['app_id', 'Identificador de la app'], ['app_secret', 'Clave secreta de la app'], ['config_id', 'Configuration ID (opcional)']],
}

function Guide({ platform, redirect }) {
  if (platform === 'youtube') return (
    <ol className="guide small">
      <li>Entra a <a href="https://console.cloud.google.com/" target="_blank" rel="noreferrer">Google Cloud Console</a> y crea un proyecto.</li>
      <li>En <i>APIs y servicios → Biblioteca</i>, habilita <b>YouTube Data API v3</b>.</li>
      <li>En <i>Pantalla de consentimiento de OAuth</i> elige «Externo» y agrega tu correo. Cambia el estado a <b>En producción</b>; si la dejas en «Prueba», tendrás que reconectar cada 7 días. Al conectar verás «Google no verificó esta app»: es tu propia app, puedes continuar.</li>
      <li>En <i>Credenciales → Crear ID de cliente OAuth</i>, elige tipo <b>App de escritorio</b> y pega aquí el ID y el secreto. No hace falta registrar ninguna URL de regreso.</li>
      <li>Para que los videos salgan <b>públicos</b>, pide la <a href="https://support.google.com/youtube/contact/yt_api_form" target="_blank" rel="noreferrer">auditoría de la API de YouTube</a>. Es gratis; hasta que la aprueben, YouTube los deja en privado.</li>
    </ol>
  )
  if (platform === 'tiktok') return (
    <ol className="guide small">
      <li>En <a href="https://developers.tiktok.com/" target="_blank" rel="noreferrer">TikTok for Developers</a>, entra a <i>Manage apps</i> y crea tu app (te pide ícono, descripción y enlaces de términos y privacidad).</li>
      <li>Agrega los productos <b>Login Kit</b> y <b>Content Posting API</b> (activa <i>Direct Post</i> si quieres publicar directo).</li>
      <li>En Login Kit, plataforma <b>Desktop</b>, registra esta URL de regreso (el <code>*</code> acepta cualquier puerto):<br /><code className="copyable">{redirect.replace(/:\d+\//, ':*/')}</code></li>
      <li>Permisos: <code>user.info.basic</code>, <code>video.upload</code> y <code>video.publish</code>.</li>
      <li>Pega aquí el Client key y el Client secret. Mientras TikTok revisa tu app, usa el modo Sandbox y agrega tu cuenta como usuario de prueba.</li>
      <li><b>Borrador en bandeja</b> funciona sin auditoría: eliges el audio en tendencia en la app y publicas. La publicación directa queda privada hasta que auditen tu app.</li>
    </ol>
  )
  return (
    <ol className="guide small">
      <li>Tu Instagram debe ser una <b>cuenta profesional</b> (Creador o Empresa) <b>vinculada a una página de Facebook</b>.</li>
      <li>En <a href="https://developers.facebook.com/apps/" target="_blank" rel="noreferrer">Meta for Developers</a>, crea una app tipo <b>Empresa</b> con el caso de uso de Instagram (inicio de sesión con Facebook).</li>
      <li>Agrega <b>Facebook Login for Business</b> y, en <i>URI de redireccionamiento OAuth válidos</i>, pega:<br /><code className="copyable">{redirect}</code></li>
      <li>Permisos: <code>instagram_basic</code>, <code>instagram_content_publish</code>, <code>pages_show_list</code>, <code>pages_read_engagement</code>, <code>business_management</code>. En modo desarrollo funcionan para ti (administrador de la app) sin revisión.</li>
      <li>En <i>Configuración → Básica</i> copia el identificador y la clave secreta de la app. Si creaste una configuración de Login for Business, pega también su ID.</li>
    </ol>
  )
}

function PlatformCard({ p, data, notify, reload }) {
  const [open, setOpen] = useState(!data.configured)
  const [form, setForm] = useState(data.creds)
  const [busy, setBusy] = useState(false)
  useEffect(() => setForm(data.creds), [data.creds])
  const save = async () => {
    try { await pub.saveCreds(p, form); notify('Credenciales guardadas'); setOpen(false); reload() } catch (e) { notify(e.message, 'error') }
  }
  const connect = async () => {
    setBusy(true)
    try { const { url } = await pub.oauthStart(p); window.location.href = url } catch (e) { notify(e.message, 'error'); setBusy(false) }
  }
  return (
    <div className="panel pub-card">
      <div className="pub-card-head">
        <h3>{NAMES[p]}</h3>
        <span className={`badge ${data.accounts.length ? 'badge-ok' : ''}`}>{data.accounts.length ? 'Conectado' : data.configured ? 'Listo para conectar' : 'Sin configurar'}</span>
      </div>
      {data.accounts.map((a) => (
        <div key={a.id} className="pub-acc">
          {a.avatar ? <img src={a.avatar} alt="" /> : <span className="pub-acc-ph"><Icon name="send" size={14} /></span>}
          <div><b>{a.name}</b>{a.extra?.page && <div className="muted small">Página: {a.extra.page}</div>}</div>
          <button className="icon-btn sm" title="Desconectar" onClick={async () => {
            if (!window.confirm(`¿Desconectar ${a.name}?`)) return
            await pub.removeAccount(a.id); reload()
          }}><Icon name="trash" size={14} /></button>
        </div>
      ))}
      <div className="row mt">
        <button className="btn btn-primary" disabled={!data.configured || busy} onClick={connect}>
          {busy ? 'Abriendo…' : data.accounts.length ? '+ Otra cuenta' : 'Conectar cuenta'}
        </button>
        <button className="btn" onClick={() => setOpen(!open)}>{open ? 'Ocultar' : 'Configurar app'}</button>
      </div>
      {open && (
        <div className="mt">
          <Guide platform={p} redirect={data.redirect_uri} />
          {FIELDS[p].map(([k, l]) => (
            <div key={k}>
              <label className="field-label">{l}</label>
              <input className="input full" value={form[k] || ''} onChange={(e) => setForm({ ...form, [k]: e.target.value })}
                type={k.includes('secret') ? 'password' : 'text'} autoComplete="off" />
            </div>
          ))}
          <button className="btn full mt" onClick={save}>Guardar credenciales</button>
          <p className="muted small mt">Se guardan solo en tu PC (data\clipper.db) y solo se usan para hablar con {NAMES[p]}.</p>
        </div>
      )}
    </div>
  )
}

function ProfileForm({ profile, notify }) {
  const [f, setF] = useState(profile)
  useEffect(() => setF(profile), [profile])
  const save = async () => { try { await pub.saveProfile(f); notify('Perfil guardado: la IA lo usará en los textos') } catch (e) { notify(e.message, 'error') } }
  return (
    <div className="panel">
      <h2>Tu canal</h2>
      <p className="muted small">La IA usa esto para escribir títulos, descripciones y hashtags con tu nicho y tu voz.</p>
      <div className="pub-profile">
        <div><label className="field-label">Nicho / de qué trata tu contenido</label>
          <input className="input full" value={f.niche} onChange={(e) => setF({ ...f, niche: e.target.value })} placeholder="Gaming en PC, emuladores, reacciones…" /></div>
        <div><label className="field-label">Audiencia</label>
          <input className="input full" value={f.audience} onChange={(e) => setF({ ...f, audience: e.target.value })} /></div>
        <div><label className="field-label">Tono</label>
          <input className="input full" value={f.tone} onChange={(e) => setF({ ...f, tone: e.target.value })} /></div>
        <div><label className="field-label">Hashtags de tu marca (siempre se incluyen)</label>
          <input className="input full" value={f.brand_hashtags} onChange={(e) => setF({ ...f, brand_hashtags: e.target.value })} placeholder="#minombre" /></div>
        <div className="span2"><label className="field-label">Llamado a la acción (se añade al final)</label>
          <input className="input full" value={f.cta} onChange={(e) => setF({ ...f, cta: e.target.value })} placeholder="Sígueme para más clips y únete a mis directos en Twitch" /></div>
      </div>
      <button className="btn mt" onClick={save}>Guardar perfil</button>
    </div>
  )
}

function PostRow({ p, notify, reload }) {
  const [edit, setEdit] = useState(false)
  const [text, setText] = useState(p.meta.caption ?? p.meta.description ?? '')
  const [when, setWhen] = useState((p.scheduled_at || '').replace(' ', 'T').slice(0, 16))
  const act = async (fn, msg) => { try { await fn(); if (msg) notify(msg); reload() } catch (e) { notify(e.message, 'error') } }
  const [label, cls] = STATUS[p.status] || [p.status, '']
  const key = p.platform === 'youtube' ? 'description' : 'caption'
  return (
    <div className="pub-post">
      <div className="pub-post-main">
        <span className={`plat plat-${p.platform}`}>{NAMES[p.platform]}</span>
        <div className="pub-post-title">
          <b>{p.title || p.meta.caption?.slice(0, 80) || 'Sin título'}</b>
          <div className="muted small">{p.account?.name || 'cuenta desconectada'} · {p.status === 'scheduled' ? `programada para ${p.scheduled_at?.slice(0, 16)}` : p.published_at ? `publicada ${p.published_at.slice(0, 16)}` : p.created_at.slice(0, 16)}</div>
        </div>
        <span className={`badge ${cls}`}>{label}</span>
      </div>
      {p.status === 'publishing' && <div className="progress"><div style={{ width: `${p.progress}%` }} /></div>}
      {p.status === 'publishing' && <div className="muted small">{p.stage}</div>}
      {p.error && <div className="err small">{p.error}</div>}
      {p.result?.note && <div className="note small">{p.result.note}</div>}
      {p.platform === 'tiktok' && p.status === 'published' && (
        <button className="link-btn small" onClick={() => navigator.clipboard?.writeText(p.meta.caption || '').then(() => notify('Texto copiado: pégalo en TikTok'))}>Copiar texto para TikTok</button>
      )}
      {edit && (
        <div className="mt">
          <textarea className="input full" rows={4} value={text} onChange={(e) => setText(e.target.value)} />
          {p.status === 'scheduled' && <input type="datetime-local" className="input full mt" value={when} onChange={(e) => setWhen(e.target.value)} />}
          <button className="btn sm mt" onClick={() => act(() => pub.edit(p.id, { meta: { [key]: text }, ...(p.status === 'scheduled' ? { scheduled_at: when } : {}) }), 'Cambios guardados').then(() => setEdit(false))}>Guardar</button>
        </div>
      )}
      <div className="row mt">
        {p.remote_url && <a className="btn sm" href={p.remote_url} target="_blank" rel="noreferrer">Ver publicación</a>}
        {['scheduled', 'failed', 'cancelled'].includes(p.status) && <button className="btn sm" onClick={() => setEdit(!edit)}>Editar</button>}
        {p.status === 'scheduled' && <button className="btn sm" onClick={() => act(() => pub.now(p.id), 'Publicando ahora')}>Publicar ya</button>}
        {['failed', 'cancelled'].includes(p.status) && <button className="btn sm" onClick={() => act(() => pub.now(p.id), 'Reintentando')}>Reintentar</button>}
        {['scheduled', 'queued'].includes(p.status) && <button className="btn sm" onClick={() => act(() => pub.cancel(p.id), 'Cancelada')}>Cancelar</button>}
        {p.status !== 'publishing' && <button className="icon-btn sm" title="Quitar de la lista" onClick={() => act(() => pub.remove(p.id))}><Icon name="trash" size={14} /></button>}
      </div>
    </div>
  )
}

export default function Publish({ navigate, notify, hash }) {
  const [status, setStatus] = useState(null)
  const [posts, setPosts] = useState([])
  const [sources, setSources] = useState([])
  const [filter, setFilter] = useState('pending')
  const [compose, setCompose] = useState(null)
  const [picking, setPicking] = useState(false)

  const load = useCallback(() => {
    pub.status().then(setStatus).catch((e) => notify(e.message, 'error'))
    pub.posts().then(setPosts).catch(() => {})
  }, [notify])
  useEffect(() => { load() }, [load])
  useEffect(() => { if (hash.includes('connected=1')) notify('Cuenta conectada') }, [hash, notify])
  useEffect(() => {
    if (!posts.some((p) => ['publishing', 'queued'].includes(p.status))) return
    const t = setInterval(() => pub.posts().then(setPosts).catch(() => {}), 3000)
    return () => clearInterval(t)
  }, [posts])

  const shown = posts.filter((p) => filter === 'all' || (filter === 'pending' ? ['scheduled', 'queued', 'publishing'].includes(p.status) : p.status === filter))
  const count = (f) => posts.filter((p) => (f === 'pending' ? ['scheduled', 'queued', 'publishing'].includes(p.status) : p.status === f)).length

  return (
    <div className="page">
      <header className="page-head">
        <div>
          <h1>Publicar en redes</h1>
          <p className="muted">Sube tus clips y videos a YouTube, TikTok e Instagram con títulos, descripciones y hashtags escritos por la IA. Revisa, edita y publica al momento o programa.</p>
        </div>
        <button className="btn btn-primary" onClick={() => { setPicking(true); pub.sources().then(setSources).catch((e) => notify(e.message, 'error')) }}>
          <Icon name="send" size={16} /> Nueva publicación
        </button>
      </header>

      {picking && (
        <div className="panel">
          <div className="pub-card-head"><h2>¿Qué video publicas?</h2><button className="icon-btn" onClick={() => setPicking(false)}><Icon name="x" /></button></div>
          {sources.length === 0 ? <p className="muted">No hay videos exportados todavía. Exporta un clip o un video final del Studio.</p> : (
            <div className="pub-sources">
              {sources.map((s) => (
                <button key={`${s.kind}${s.id}`} className="pub-source" onClick={() => { setCompose(s); setPicking(false) }}>
                  <video src={s.url} preload="metadata" muted />
                  <span className="small"><b>{s.title}</b></span>
                  <span className="muted small">{s.kind === 'studio_render' ? 'Studio' : 'Clip'} · {fmtDuration(s.duration)} · {s.created_at?.slice(0, 16)}</span>
                </button>
              ))}
            </div>
          )}
        </div>
      )}

      <div className="panel">
        <div className="pub-card-head">
          <h2>Publicaciones</h2>
          <div className="seg">
            {[['pending', 'Pendientes'], ['published', 'Publicadas'], ['failed', 'Con error'], ['all', 'Todas']].map(([k, l]) => (
              <button key={k} className={filter === k ? 'on' : ''} onClick={() => setFilter(k)}>{l}{k !== 'all' && count(k) ? ` (${count(k)})` : ''}</button>
            ))}
          </div>
        </div>
        {shown.length === 0 ? <p className="muted small">Nada por aquí.</p> : shown.map((p) => <PostRow key={p.id} p={p} notify={notify} reload={load} />)}
      </div>

      <h2 className="mt">Cuentas</h2>
      <p className="muted small">Se conectan con la API oficial de cada red usando tus propias apps de desarrollador (gratis). Pulsa «Configurar app» y sigue los pasos una sola vez.</p>
      <div className="pub-cards">
        {status && ['youtube', 'tiktok', 'instagram'].map((p) => <PlatformCard key={p} p={p} data={status.platforms[p]} notify={notify} reload={load} />)}
      </div>

      {status && <ProfileForm profile={status.profile} notify={notify} />}

      {compose && <PublishComposer source={compose} notify={notify} navigate={navigate}
        onClose={(sent) => { setCompose(null); if (sent) load() }} />}
    </div>
  )
}
