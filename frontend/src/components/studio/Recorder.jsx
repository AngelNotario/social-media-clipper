import { useEffect, useRef, useState } from 'react'
import Icon from '../Icon.jsx'

/** Graba con la cámara y/o el micrófono directamente en el navegador. */
export default function Recorder({ onRecorded }) {
  const [mode, setMode] = useState('video') // video | audio
  const [stream, setStream] = useState(null)
  const [state, setState] = useState('idle') // idle | ready | countdown | recording | done
  const [count, setCount] = useState(0)
  const [elapsed, setElapsed] = useState(0)
  const [blobUrl, setBlobUrl] = useState(null)
  const [err, setErr] = useState(null)
  const live = useRef(null)
  const rec = useRef(null)
  const chunks = useRef([])
  const blobRef = useRef(null)
  const timer = useRef(null)

  const stop = (s) => s?.getTracks().forEach((t) => t.stop())

  useEffect(() => () => { stop(stream); clearInterval(timer.current) }, [stream])

  const enable = async () => {
    setErr(null)
    try {
      stop(stream)
      const s = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true },
        video: mode === 'video' ? { width: { ideal: 1920 }, height: { ideal: 1080 } } : false,
      })
      setStream(s)
      setState('ready')
      setTimeout(() => { if (live.current) live.current.srcObject = s }, 0)
    } catch (e) {
      setErr(`No se pudo acceder a la ${mode === 'video' ? 'cámara/micrófono' : 'micrófono'}: ${e.message}`)
    }
  }

  const start = () => {
    setState('countdown')
    let c = 3
    setCount(c)
    const t = setInterval(() => {
      c -= 1
      setCount(c)
      if (c <= 0) { clearInterval(t); begin() }
    }, 800)
  }

  const begin = () => {
    chunks.current = []
    const types = mode === 'video'
      ? ['video/webm;codecs=vp9,opus', 'video/webm;codecs=vp8,opus', 'video/webm']
      : ['audio/webm;codecs=opus', 'audio/webm']
    const mimeType = types.find((t) => MediaRecorder.isTypeSupported(t)) || ''
    const r = new MediaRecorder(stream, mimeType ? { mimeType, videoBitsPerSecond: 8_000_000 } : undefined)
    r.ondataavailable = (e) => e.data.size && chunks.current.push(e.data)
    r.onstop = () => {
      const blob = new Blob(chunks.current, { type: mimeType || 'video/webm' })
      blobRef.current = blob
      setBlobUrl(URL.createObjectURL(blob))
      setState('done')
    }
    r.start(1000)
    rec.current = r
    setElapsed(0)
    const t0 = Date.now()
    timer.current = setInterval(() => setElapsed((Date.now() - t0) / 1000), 250)
    setState('recording')
  }

  const finish = () => { rec.current?.stop(); clearInterval(timer.current) }
  const retake = () => { setBlobUrl(null); blobRef.current = null; setState('ready'); setTimeout(() => { if (live.current) live.current.srcObject = stream }, 0) }
  const use = () => { stop(stream); onRecorded(blobRef.current, mode) }

  const mm = `${Math.floor(elapsed / 60)}:${String(Math.floor(elapsed % 60)).padStart(2, '0')}`

  return (
    <div className="recorder">
      {state === 'idle' && (
        <>
          <div className="seg">
            <button className={mode === 'video' ? 'on' : ''} onClick={() => setMode('video')}>Cámara + micrófono</button>
            <button className={mode === 'audio' ? 'on' : ''} onClick={() => setMode('audio')}>Solo voz</button>
          </div>
          <p className="muted small">Graba tal cual: la IA quitará silencios y muletillas. Habla con naturalidad.</p>
          <button className="btn btn-primary" onClick={enable}><Icon name="play" /> Activar {mode === 'video' ? 'cámara' : 'micrófono'}</button>
        </>
      )}
      {err && <div className="alert alert-error">{err}</div>}
      {stream && state !== 'idle' && state !== 'done' && (
        <div className="rec-stage">
          {mode === 'video'
            ? <video ref={live} autoPlay muted playsInline className="rec-live" />
            : <div className="rec-audio"><span className={`rec-dot ${state === 'recording' ? 'on' : ''}`} /> Micrófono listo</div>}
          {state === 'countdown' && <div className="rec-count">{count}</div>}
          {state === 'recording' && <div className="rec-badge"><span className="rec-dot on" /> REC {mm}</div>}
        </div>
      )}
      {state === 'ready' && <button className="btn btn-primary" onClick={start}>● Empezar a grabar</button>}
      {state === 'recording' && <button className="btn btn-danger" onClick={finish}>■ Detener</button>}
      {state === 'done' && blobUrl && (
        <>
          {mode === 'video' ? <video src={blobUrl} controls className="rec-live" /> : <audio src={blobUrl} controls />}
          <div className="row">
            <button className="btn" onClick={retake}>Repetir toma</button>
            <button className="btn btn-primary" onClick={use}><Icon name="check" /> Usar esta toma</button>
          </div>
        </>
      )}
    </div>
  )
}
