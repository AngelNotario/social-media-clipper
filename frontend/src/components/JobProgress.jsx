import { api } from '../api.js'

export default function JobProgress({ job, compact = false }) {
  if (!job) return null
  const active = job.status === 'queued' || job.status === 'running'
  return (
    <div className={`job ${job.status} ${compact ? 'compact' : ''}`}>
      <div className="job-row">
        <span className="job-stage">
          {job.status === 'error' ? `Error: ${job.error}` : job.stage}
        </span>
        {active && <span className="job-pct">{Math.round(job.progress)}%</span>}
        {active && (
          <button className="link-btn" onClick={() => api.cancelJob(job.id)}>Cancelar</button>
        )}
      </div>
      {active && (
        <div className="bar"><div className="bar-fill" style={{ width: `${Math.max(job.progress, 2)}%` }} /></div>
      )}
    </div>
  )
}
