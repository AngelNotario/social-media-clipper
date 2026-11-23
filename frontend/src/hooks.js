import { useEffect, useRef, useState } from 'react'
import { api } from './api.js'

/** Sigue un trabajo en segundo plano hasta que termine. */
export function useJob(jobId, onDone) {
  const [job, setJob] = useState(null)
  const doneRef = useRef(onDone)
  doneRef.current = onDone

  useEffect(() => {
    if (!jobId) { setJob(null); return }
    let alive = true
    let timer
    const tick = async () => {
      try {
        const j = await api.job(jobId)
        if (!alive) return
        setJob(j)
        if (['done', 'error', 'cancelled'].includes(j.status)) {
          doneRef.current?.(j)
          return
        }
      } catch { /* reintenta */ }
      timer = setTimeout(tick, 1200)
    }
    tick()
    return () => { alive = false; clearTimeout(timer) }
  }, [jobId])

  return job
}
