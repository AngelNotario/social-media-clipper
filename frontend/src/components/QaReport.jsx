// Resultado del control de calidad automático de un video renderizado.
const VERDICT = { listo: 'Listo para publicar', revisar: 'Conviene revisar', rehacer: 'Mejor rehacerlo' }

const tc = (t) => `${Math.floor(t / 60)}:${String(Math.floor(t % 60)).padStart(2, '0')}`

export default function QaReport({ qa, audio, onSeek }) {
  if (!qa || qa.score == null) return null
  const cls = qa.verdict === 'listo' ? 'qa-ok' : qa.verdict === 'revisar' ? 'qa-warn' : 'qa-bad'
  return (
    <details className={`qa ${cls}`}>
      <summary>
        <span className="qa-score">{qa.score}</span>
        <span className="small">{VERDICT[qa.verdict] || qa.verdict}</span>
        {qa.issues.length > 0 && <span className="muted small">· {qa.issues.length} aviso{qa.issues.length > 1 ? 's' : ''}</span>}
        {audio?.applied && <span className="muted small">· audio {audio.lufs} LUFS</span>}
      </summary>
      {qa.issues.length === 0
        ? <div className="muted small">Sin problemas: sin negros, congelados ni silencios largos, y el gancho habla desde el inicio.</div>
        : (
          <ul className="qa-list">
            {qa.issues.map((i, k) => (
              <li key={k} className={`sev-${i.severity}`}>
                {i.t != null && (onSeek
                  ? <button className="qa-t" onClick={() => onSeek(i.t)}>{tc(i.t)}</button>
                  : <span className="qa-t">{tc(i.t)}</span>)}
                <span className="small">{i.message}</span>
              </li>
            ))}
          </ul>
        )}
    </details>
  )
}
