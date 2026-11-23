import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import Icon from './Icon.jsx'
import { fmtDuration, pub } from '../api.js'

const NAMES = { youtube: 'YouTube', tiktok: 'TikTok', instagram: 'Instagram' }
const CATS = [['gaming', 'Videojuegos'], ['entretenimiento', 'Entretenimiento'], ['comedia', 'Comedia'], ['gente_y_blogs', 'Gente y blogs'],
  ['educacion', 'Educación'], ['ciencia_y_tecnologia', 'Ciencia y tecnología'], ['tutoriales', 'Tutoriales'], ['deportes', 'Deportes'],
  ['musica', 'Música'], ['noticias_y_politica', 'Noticias y política']]
const CAT_ID = { gaming: '20', entretenimiento: '24', comedia: '23', gente_y_blogs: '22', educacion: '27', ciencia_y_tecnologia: '28',
  tutoriales: '26', deportes: '17', musica: '10', noticias_y_politica: '25' }

export function fileUrl(kind, id) {
  return kind === 'studio_render' ? `/api/studio/renders/${id}/file` : `/api/renders/${id}/file`
}

function Counter({ value, max }) {
  const n = (value || '').length
  return <span className={`small ${n > max ? 'err' : 'muted'}`}>{n}/{max}</span>
}

// Caja de texto que crece con el contenido (hasta 60 % de la pantalla) y se puede ampliar a pantalla completa.
function AutoText({ value, onChange, minRows = 4, label, max }) {
  const ref = useRef(null)
  const [big, setBig] = useState(false)
  useLayoutEffect(() => {
    const el = ref.current
    if (!el || big) return
    el.style.height = 'auto'
    el.style.height = `${Math.min(el.scrollHeight + 4, window.innerHeight * 0.6)}px`
  }, [value, big])
  return (
    <>
      <div className="auto-text">
        <textarea ref={ref} className="input full" rows={minRows} value={value || ''} onChange={(e) => onChange(e.target.value)} />
        <button className="link-btn small auto-text-big" title="Ampliar para editar cómodo" onClick={() => setBig(true)}>⤢ Ampliar</button>
      </div>
      {big && (
        <div className="auto-text-modal" onClick={() => setBig(false)}>
          <div className="auto-text-card" onClick={(e) => e.stopPropagation()}>
            <div className="lbl-row"><b>{label}</b>{max ? <Counter value={value} max={max} /> : null}</div>
            <textarea className="input full" autoFocus value={value || ''} onChange={(e) => onChange(e.target.value)} />
            <div className="row-end"><button className="btn btn-primary" onClick={() => setBig(false)}>Listo</button></div>
          </div>
        </div>
      )}
    </>
  )
}

// Etiquetas como "chips": × para quitar, Enter o coma para agregar.
function Chips({ items, onChange, placeholder, prefix = '' }) {
  const [draft, setDraft] = useState('')
  const add = (raw) => {
    const vals = raw.split(',').map((x) => x.trim().replace(/^#/, '')).filter(Boolean)
    if (vals.length) onChange([...items, ...vals.filter((v) => !items.some((i) => i.toLowerCase() === v.toLowerCase()))])
    setDraft('')
  }
  return (
    <div className="chip-input">
      {items.map((t, i) => (
        <span key={`${t}${i}`} className="chip chip-x">{prefix}{t}<button onClick={() => onChange(items.filter((_, j) => j !== i))} title="Quitar">×</button></span>
      ))}
      <input value={draft} placeholder={items.length ? '' : placeholder} onChange={(e) => (e.target.value.includes(',') ? add(e.target.value) : setDraft(e.target.value))}
        onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); add(draft) } else if (e.key === 'Backspace' && !draft && items.length) onChange(items.slice(0, -1)) }}
        onBlur={() => draft && add(draft)} />
    </div>
  )
}

// Hashtags dentro del texto (TikTok/Instagram): se ven como chips y se editan sin tocar el resto del texto.
const HASH_RE = /(^|\s)#([\p{L}\p{N}_]+)/gu
function hashtagsOf(text) { return [...String(text || '').matchAll(HASH_RE)].map((m) => m[2]) }
function withHashtags(text, tags) {
  const body = String(text || '').replace(HASH_RE, '').replace(/[ \t]+\n/g, '\n').trimEnd()
  return tags.length ? `${body}\n\n${tags.map((t) => `#${t}`).join(' ')}` : body
}

// Vista previa con el aspecto de cada red (aproximado)
function Preview({ platform, meta, account, thumbUrl }) {
  const [more, setMore] = useState(false)
  if (platform === 'youtube') {
    const d = meta.youtube.description || ''
    return (
      <div className="pv-card pv-yt">
        <div className="pv-yt-thumb">{thumbUrl ? <img src={thumbUrl} alt="" /> : <span>▶</span>}</div>
        <div className="pv-yt-title">{meta.youtube.title || 'Sin título'}</div>
        <div className="muted small">{account || 'Tu canal'} · {meta.youtube.is_short ? 'Short' : 'Video'}</div>
        <div className={`pv-desc ${more ? 'open' : ''}`}>{d}</div>
        {d.length > 140 && <button className="link-btn small" onClick={() => setMore(!more)}>{more ? 'Mostrar menos' : '...más'}</button>}
      </div>
    )
  }
  const cap = platform === 'tiktok' ? meta.tiktok.caption : meta.instagram.caption
  const html = String(cap || '').split(/(#[\p{L}\p{N}_]+)/u).map((part, i) => (part.startsWith('#') ? <b key={i} className="pv-tag">{part}</b> : part))
  return (
    <div className={`pv-card ${platform === 'tiktok' ? 'pv-tt' : 'pv-ig'}`}>
      <div className="pv-head"><span className="pv-avatar" />{account || (platform === 'tiktok' ? '@tu_cuenta' : 'tu_cuenta')}</div>
      <div className={`pv-desc ${more ? 'open' : ''}`}>{platform === 'instagram' && <b>{account || 'tu_cuenta'} </b>}{html}</div>
      {String(cap || '').length > 110 && <button className="link-btn small" onClick={() => setMore(!more)}>{more ? 'menos' : '... más'}</button>}
      {platform === 'instagram' && meta.instagram.audio_name && <div className="muted small">♫ {meta.instagram.audio_name}</div>}
    </div>
  )
}

function copy(text, notify) {
  navigator.clipboard?.writeText(text).then(() => notify?.('Copiado'), () => {})
}

// Revisa, edita y publica (o programa) un video en YouTube, TikTok e Instagram con metadatos escritos por la IA.
export default function PublishComposer({ source, onClose, notify, navigate }) {
  const [status, setStatus] = useState(null)
  const [info, setInfo] = useState(null)
  const [meta, setMeta] = useState(null)
  const [times, setTimes] = useState({})
  const [loading, setLoading] = useState(true)
  const [notes, setNotes] = useState('')
  const [on, setOn] = useState({})
  const [acc, setAcc] = useState({})
  const [tab, setTab] = useState('youtube')
  const [when, setWhen] = useState('now') // now | same | suggested
  const [at, setAt] = useState('')
  const [yt, setYt] = useState({ privacy: 'public' })
  const [tt, setTt] = useState({ mode: 'inbox', disable_comment: false, disable_duet: false, disable_stitch: false, is_aigc: false })
  const [ig, setIg] = useState({ share_to_feed: true })
  const [sending, setSending] = useState(false)
  const [edited, setEdited] = useState({}) // campos que escribiste tú: la IA nunca los pisa
  const [confirm, setConfirm] = useState(false)
  const [restored, setRestored] = useState(false)
  const draftKey = `smc_pub_draft_${source.kind}_${source.id}`
  const [thumbs, setThumbs] = useState([])
  const [thumbId, setThumbId] = useState(null)
  useEffect(() => {
    fetch(`/api/thumbs?kind=${source.kind}&id=${source.id}`).then((r) => r.json()).then((list) => {
      setThumbs(list || [])
      const best = (list || []).find((t) => t.chosen) || [...(list || [])].sort((a, b) => (b.score || 0) - (a.score || 0))[0]
      if (best) setThumbId(best.id)
    }).catch(() => {})
  }, [source.kind, source.id])

  // borrador: si cierras o se recarga la ventana, tus textos siguen ahí (no se vuelven a generar)
  useEffect(() => {
    if (!meta) return
    try { localStorage.setItem(draftKey, JSON.stringify({ t: Date.now(), meta, edited, notes, yt, tt, ig, info, times })) } catch { /* */ }
  }, [meta, edited, notes, yt, tt, ig]) // eslint-disable-line react-hooks/exhaustive-deps

  const generate = async (n = notes) => {
    setLoading(true)
    try {
      const r = await pub.metadata(source.kind, source.id, n)
      setInfo(r.source); setTimes(r.suggested_times)
      setMeta((old) => {
        if (!old) return r.meta
        const out = { ...r.meta }
        for (const key of Object.keys(edited)) { // conserva lo que editaste a mano
          const [plat, field] = key.split('.')
          out[plat] = { ...out[plat], [field]: old[plat][field] }
          if (field === 'tags_text') out[plat].tags = old[plat].tags
        }
        return out
      })
      setAt((x) => x || r.suggested_times.tiktok)
      if (Object.keys(edited).length) notify('Regeneré solo lo que no habías editado')
      if (r.meta.warning) notify(r.meta.warning, 'error')
    } catch (e) { notify(e.message, 'error') }
    setLoading(false)
  }

  useEffect(() => {
    pub.status().then((s) => {
      setStatus(s)
      const o = {}; const a = {}
      for (const p of ['youtube', 'tiktok', 'instagram']) {
        const list = s.platforms[p].accounts
        o[p] = list.length > 0; if (list.length) a[p] = list[0].id
      }
      setOn(o); setAcc(a)
      setTab(['youtube', 'tiktok', 'instagram'].find((p) => o[p]) || 'youtube')
    }).catch((e) => notify(e.message, 'error'))
    let d = null
    try { d = JSON.parse(localStorage.getItem(draftKey) || 'null') } catch { /* */ }
    if (d && Date.now() - d.t < 3 * 86400000 && d.meta) {
      setMeta(d.meta); setEdited(d.edited || {}); setNotes(d.notes || ''); setInfo(d.info || null); setTimes(d.times || {})
      if (d.yt) setYt(d.yt); if (d.tt) setTt(d.tt); if (d.ig) setIg(d.ig)
      setAt((x) => x || d.times?.tiktok || '')
      setRestored(true); setLoading(false)
      pub.source(source.kind, source.id).then(setInfo).catch(() => {})
    } else generate('')
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [source.kind, source.id])

  const set = (p, k, v) => {
    setEdited((e) => ({ ...e, [`${p}.${k}`]: true }))
    setMeta((m) => ({ ...m, [p]: { ...m[p], [k]: v } }))
  }
  const chosen = useMemo(() => Object.keys(on).filter((p) => on[p] && acc[p]), [on, acc])

  const buildTargets = () => chosen.map((p) => {
      let m; let options
      if (p === 'youtube') {
        const tags = meta.youtube.tags_text != null ? meta.youtube.tags_text.split(',').map((x) => x.trim()).filter(Boolean) : meta.youtube.tags
        m = { title: meta.youtube.title, description: meta.youtube.description, tags,
          category_id: CAT_ID[meta.youtube.category] || meta.youtube.category_id, language: meta.youtube.language }
        options = { ...yt, ...(thumbId ? { thumbnail_id: thumbId } : {}) }
      } else if (p === 'tiktok') { m = { caption: meta.tiktok.caption }; options = tt } else {
        m = { caption: meta.instagram.caption, audio_name: meta.instagram.audio_name }; options = ig
      }
      const t = { platform: p, account_id: acc[p], meta: m, options }
      if (when === 'suggested') t.scheduled_at = times[p]
      return t
    })

  const send = async () => {
    if (!chosen.length) { notify('Elige al menos una red con cuenta conectada', 'error'); return }
    const targets = buildTargets()
    setConfirm(false)
    setSending(true)
    try {
      await pub.create({ source_kind: source.kind, source_id: source.id, targets, scheduled_at: when === 'same' ? at : null })
      notify(when === 'now' ? `Subiendo a ${chosen.map((p) => NAMES[p]).join(', ')}…` : 'Publicación programada')
      try { localStorage.removeItem(draftKey) } catch { /* */ }
      onClose(true)
    } catch (e) { notify(e.message, 'error') }
    setSending(false)
  }

  const P = status?.platforms
  return (
    <div className="modal-backdrop" onClick={() => onClose(false)}>
      <div className="modal publish-modal" onClick={(e) => e.stopPropagation()}>
        <button className="icon-btn modal-close" onClick={() => onClose(false)}><Icon name="x" /></button>
        <div className="publish-left">
          <video src={fileUrl(source.kind, source.id)} controls preload="metadata" className={info?.vertical ? 'v' : ''} />
          {info && <div className="muted small">{info.title} · {fmtDuration(info.duration)} · {info.vertical ? 'vertical' : 'horizontal'}</div>}
          <label className="field-label mt">Indicaciones para la IA (opcional)</label>
          <textarea className="input full" rows={2} value={notes} onChange={(e) => setNotes(e.target.value)}
            placeholder="Ej.: enfócalo en el chiste del final, menciona a mi comunidad «los tenshis»" />
          <button className="btn full mt" disabled={loading} onClick={() => generate()}>{loading ? 'La IA está escribiendo…' : '✨ Regenerar textos'}</button>
          {meta?.model && <div className="muted small mt">Escrito por {meta.model}</div>}
          <div className="pub-thumb">
            <div className="lbl-row"><label className="field-label">Miniatura</label>
              <button className="link-btn small" onClick={() => { onClose(false); window.location.hash = `#/thumbs?kind=${source.kind}&id=${source.id}` }}>{thumbs.length ? 'Crear más' : 'Crear con IA'}</button></div>
            {thumbs.length === 0 ? <div className="muted small">Aún no hay miniaturas para este video. Créalas en «Miniaturas» (la IA investiga en internet y diseña varias).</div> : (
              <div className="pub-thumbs">
                {thumbs.slice(0, 6).map((t) => (
                  <img key={t.id} src={`${t.url}?v=${encodeURIComponent(t.path)}`} alt="" className={t.id === thumbId ? 'on' : ''} title={t.score ? `${t.score}/10` : ''} onClick={() => setThumbId(t.id)} />
                ))}
              </div>
            )}
            <div className="muted small">Se pone en YouTube al subir (requiere canal verificado). En TikTok e Instagram la API no permite subir portada: descárgala y ponla en la app.</div>
          </div>
          {meta?.thumbnail_text && <div className="muted small">Texto sugerido para portada: <b>{meta.thumbnail_text}</b></div>}
          {meta?.doubts?.length > 0 && <div className="note small">⚠ Según internet, ojo con: {meta.doubts.slice(0, 3).map((d) => String(d).slice(0, 140)).join(' · ')}</div>}
          {meta?.sources?.length > 0 && (
            <details className="small"><summary className="muted">Fuentes consultadas ({meta.sources.length})</summary>
              <ul className="pub-sources">{meta.sources.map((s, i) => <li key={i}><a href={s.url} target="_blank" rel="noreferrer">{s.title || s.url}</a> {s.date ? <span className="muted">· {String(s.date).slice(0, 10)}</span> : null}</li>)}</ul>
            </details>
          )}
          {meta?.trending?.length > 0 && (
            <details className="small"><summary className="muted">Lo que funciona hoy en el tema</summary>
              <ul className="pub-sources">{meta.trending.map((t, i) => <li key={i}><a href={t.url} target="_blank" rel="noreferrer">{t.title}</a> <span className="muted">· {Number(t.views || 0).toLocaleString()} vistas</span></li>)}</ul>
            </details>
          )}
        </div>

        <div className="publish-right">
          <h2>Publicar en redes</h2>
          <div className="pub-platforms">
            {['youtube', 'tiktok', 'instagram'].map((p) => {
              const list = P?.[p]?.accounts || []
              return (
                <div key={p} className={`pub-plat ${on[p] ? 'on' : ''} ${tab === p ? 'tab' : ''}`} onClick={() => setTab(p)}>
                  <label className="check" onClick={(e) => e.stopPropagation()}>
                    <input type="checkbox" disabled={!list.length} checked={!!on[p] && list.length > 0}
                      onChange={(e) => {
                        const next = { ...on, [p]: e.target.checked }
                        setOn(next)
                        if (e.target.checked) setTab(p) // al elegir una red se abre su pestaña
                        else if (tab === p) setTab(['youtube', 'tiktok', 'instagram'].find((x) => x !== p && next[x]) || tab)
                      }} /> <b>{NAMES[p]}</b>
                  </label>
                  {list.length > 1 ? (
                    <select className="input sm" value={acc[p] || ''} onClick={(e) => e.stopPropagation()} onChange={(e) => setAcc({ ...acc, [p]: Number(e.target.value) })}>
                      {list.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
                    </select>
                  ) : list.length === 1 ? <span className="muted small">{list[0].name}</span>
                    : <button className="link-btn small" onClick={() => { onClose(false); navigate('publish') }}>Conectar cuenta</button>}
                </div>
              )
            })}
          </div>

          {!meta ? <p className="muted">La IA está escribiendo títulos, descripciones y hashtags…</p> : (
            <div className="pub-fields">
              <div className="pub-tabs">
                {['youtube', 'tiktok', 'instagram'].map((p) => (
                  <button key={p} className={`pub-tab t-${p} ${tab === p ? 'on' : ''} ${on[p] ? 'sel' : ''}`} onClick={() => setTab(p)}
                    title={on[p] ? 'Se publicará en esta red' : 'No está marcada para publicar'}>
                    {NAMES[p]}{on[p] ? ' ✓' : ''}
                  </button>
                ))}
              </div>
              <div className="muted small">Cada red tiene su propio texto: lo que edites aquí solo cambia {NAMES[tab]}.</div>
              {tab === 'youtube' && (<>
                <div className="lbl-row"><label className="field-label">Título</label><Counter value={meta.youtube.title} max={100} /></div>
                <input className="input full" value={meta.youtube.title} onChange={(e) => set('youtube', 'title', e.target.value)} />
                <div className="lbl-row"><label className="field-label">Descripción</label><Counter value={meta.youtube.description} max={5000} /></div>
                <AutoText value={meta.youtube.description} onChange={(v) => set('youtube', 'description', v)} minRows={6} label="Descripción de YouTube" max={5000} />
                <div className="lbl-row"><label className="field-label">Etiquetas</label><span className="muted small">Enter o coma para agregar · × para quitar</span></div>
                <Chips items={meta.youtube.tags_text != null ? meta.youtube.tags_text.split(',').map((x) => x.trim()).filter(Boolean) : (meta.youtube.tags || [])}
                  onChange={(list) => set('youtube', 'tags_text', list.join(', '))} placeholder="Agrega etiquetas" />
                <div className="learn-row">
                  <select className="input" value={meta.youtube.category} onChange={(e) => set('youtube', 'category', e.target.value)}>
                    {CATS.map(([k, l]) => <option key={k} value={k}>{l}</option>)}
                  </select>
                  <select className="input" value={yt.privacy} onChange={(e) => setYt({ ...yt, privacy: e.target.value })}>
                    <option value="public">Público</option><option value="unlisted">No listado</option><option value="private">Privado</option>
                  </select>
                </div>
                {meta.youtube.is_short && <div className="muted small">Se sube como Short (vertical y de menos de 3 min; se añadió #shorts).</div>}
                <div className="note small">Mientras Google no audite tu proyecto, YouTube deja los videos subidos por API en privado.</div>
              </>)}

              {tab === 'tiktok' && (<>
                <div className="seg seg-full">
                  <button className={tt.mode === 'inbox' ? 'on' : ''} onClick={() => setTt({ ...tt, mode: 'inbox' })}>Borrador en bandeja</button>
                  <button className={tt.mode === 'direct' ? 'on' : ''} onClick={() => setTt({ ...tt, mode: 'direct' })}>Publicar directo</button>
                </div>
                <div className="muted small">{tt.mode === 'inbox'
                  ? 'Llega a tu bandeja de TikTok: abres la notificación, eliges un audio en tendencia, pegas el texto y publicas. Funciona sin auditoría.'
                  : 'Se publica solo. Hasta que TikTok audite tu app, queda privado (solo tú lo ves).'}</div>
                <div className="lbl-row"><label className="field-label">Texto y hashtags</label>
                  <span><Counter value={meta.tiktok.caption} max={2200} /> <button className="link-btn small" onClick={() => copy(meta.tiktok.caption, notify)}>Copiar</button></span></div>
                <AutoText value={meta.tiktok.caption} onChange={(v) => set('tiktok', 'caption', v)} minRows={5} label="Texto de TikTok" max={2200} />
                <label className="field-label">Hashtags</label>
                <Chips items={hashtagsOf(meta.tiktok.caption)} prefix="#" onChange={(list) => set('tiktok', 'caption', withHashtags(meta.tiktok.caption, list))} placeholder="Agrega hashtags" />
                {meta.tiktok.audio_tip && <div className="tip"><b>🎵 Audio sugerido:</b> {meta.tiktok.audio_tip}</div>}
                {meta.tiktok.search_terms?.length > 0 && (
                  <div className="small"><b>Lo que la gente busca</b> (dilo en el video o ponlo como texto en pantalla):
                    <div className="chips">{meta.tiktok.search_terms.map((s) => <span key={s} className="chip" onClick={() => copy(s, notify)}>{s}</span>)}</div></div>
                )}
                {tt.mode === 'direct' && (
                  <div className="checks">
                    <label className="check"><input type="checkbox" checked={!tt.disable_comment} onChange={(e) => setTt({ ...tt, disable_comment: !e.target.checked })} /> Comentarios</label>
                    <label className="check"><input type="checkbox" checked={!tt.disable_duet} onChange={(e) => setTt({ ...tt, disable_duet: !e.target.checked })} /> Dúos</label>
                    <label className="check"><input type="checkbox" checked={!tt.disable_stitch} onChange={(e) => setTt({ ...tt, disable_stitch: !e.target.checked })} /> Stitch</label>
                    <label className="check"><input type="checkbox" checked={tt.is_aigc} onChange={(e) => setTt({ ...tt, is_aigc: e.target.checked })} /> Marcar como hecho con IA</label>
                  </div>
                )}
              </>)}

              {tab === 'instagram' && (<>
                <div className="lbl-row"><label className="field-label">Texto del Reel</label><Counter value={meta.instagram.caption} max={2200} /></div>
                <AutoText value={meta.instagram.caption} onChange={(v) => set('instagram', 'caption', v)} minRows={6} label="Texto del Reel" max={2200} />
                <label className="field-label">Hashtags</label>
                <Chips items={hashtagsOf(meta.instagram.caption)} prefix="#" onChange={(list) => set('instagram', 'caption', withHashtags(meta.instagram.caption, list))} placeholder="Agrega hashtags" />
                <label className="field-label">Nombre del audio original</label>
                <input className="input full" maxLength={100} value={meta.instagram.audio_name} onChange={(e) => set('instagram', 'audio_name', e.target.value)} />
                <div className="muted small">Si otros usan tu audio, aparece con este nombre. La API de Instagram no permite poner canciones del catálogo; agrégala desde la app si la quieres.</div>
                <label className="check"><input type="checkbox" checked={ig.share_to_feed} onChange={(e) => setIg({ ...ig, share_to_feed: e.target.checked })} /> Mostrar también en el feed</label>
              </>)}
              <div className="field-label mt">Así se verá en {NAMES[tab]}</div>
              <Preview platform={tab} meta={meta} account={(P?.[tab]?.accounts || []).find((a) => a.id === acc[tab])?.name || (P?.[tab]?.accounts || [])[0]?.name}
                thumbUrl={tab === 'youtube' && thumbId ? `/api/thumbs/${thumbId}/file` : null} />
            </div>
          )}

          <div className="pub-when">
            <div className="seg seg-full">
              <button className={when === 'now' ? 'on' : ''} onClick={() => setWhen('now')}>Ahora</button>
              <button className={when === 'same' ? 'on' : ''} onClick={() => setWhen('same')}>Programar</button>
              <button className={when === 'suggested' ? 'on' : ''} onClick={() => setWhen('suggested')}>Mejor horario por red</button>
            </div>
            {when === 'same' && <input type="datetime-local" className="input full" value={at} onChange={(e) => setAt(e.target.value)} />}
            {when === 'suggested' && (
              <div className="muted small">{chosen.map((p) => `${NAMES[p]}: ${(times[p] || '').replace('T', ' ')}`).join(' · ')}
                <br />Horarios orientativos de más actividad en México/LatAm. Se publica a esa hora si la app está abierta (si la PC está apagada, al abrirla).</div>
            )}
            {when === 'same' && <div className="muted small">La app debe estar abierta a esa hora; si no, publica en cuanto la abras.</div>}
          </div>
          {restored && <div className="muted small">Borrador restaurado con tus cambios. «Regenerar» solo cambia lo que no editaste.</div>}
          <button className="btn btn-primary full" disabled={sending || loading || !chosen.length} onClick={() => setConfirm(true)}>
            <Icon name="send" size={16} /> {sending ? 'Enviando…' : when === 'now' ? `Publicar en ${chosen.length || 0} red${chosen.length === 1 ? '' : 'es'}` : 'Programar'}
          </button>
        </div>
        {confirm && (
          <div className="pub-confirm" onClick={(e) => e.stopPropagation()}>
            <h3>Así se publicará</h3>
            {buildTargets().map((t) => (
              <div key={t.platform} className="pub-confirm-item">
                <b>{NAMES[t.platform]}</b>{t.scheduled_at || (when === 'same' && at) ? ` · ${(t.scheduled_at || at).replace('T', ' ')}` : ' · ahora'}
                <Preview platform={t.platform} meta={meta} account={(P?.[t.platform]?.accounts || []).find((a) => a.id === acc[t.platform])?.name}
                  thumbUrl={t.platform === 'youtube' && thumbId ? `/api/thumbs/${thumbId}/file` : null} />
                {t.platform === 'youtube' && t.meta.tags?.length > 0 && <div className="muted small">Etiquetas: {t.meta.tags.join(', ')}</div>}
              </div>
            ))}
            <div className="row">
              <button className="btn" onClick={() => setConfirm(false)}>Volver a editar</button>
              <button className="btn btn-primary" onClick={send}>Confirmar y {when === 'now' ? 'publicar' : 'programar'}</button>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
