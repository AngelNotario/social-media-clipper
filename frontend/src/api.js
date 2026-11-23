async function request(path, options = {}) {
  const res = await fetch(`/api${path}`, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  })
  let data = null
  try { data = await res.json() } catch { /* respuesta sin cuerpo */ }
  if (!res.ok) {
    const msg = typeof data?.detail === 'string' ? data.detail : `Error ${res.status}`
    throw new Error(msg)
  }
  return data
}

const j = (method, body) => ({ method, body: body === undefined ? undefined : JSON.stringify(body) })

export const pub = {
  status: () => request('/publish/status'),
  saveCreds: (platform, fields) => request(`/publish/credentials/${platform}`, j('PUT', fields)),
  oauthStart: (platform) => request(`/publish/oauth/${platform}/start`),
  removeAccount: (id) => request(`/publish/accounts/${id}`, j('DELETE')),
  saveProfile: (p) => request('/publish/profile', j('PUT', p)),
  sources: () => request('/publish/sources'),
  source: (kind, id) => request(`/publish/source?kind=${kind}&id=${id}`),
  metadata: (kind, id, notes) => request('/publish/metadata', j('POST', { source_kind: kind, source_id: id, notes })),
  create: (body) => request('/publish/posts', j('POST', body)),
  posts: () => request('/publish/posts'),
  edit: (id, body) => request(`/publish/posts/${id}`, j('PATCH', body)),
  now: (id) => request(`/publish/posts/${id}/now`, j('POST')),
  cancel: (id) => request(`/publish/posts/${id}/cancel`, j('POST')),
  remove: (id) => request(`/publish/posts/${id}`, j('DELETE')),
}

export const api = {
  status: () => request('/system/status'),
  settings: () => request('/settings'),
  saveSettings: (folders) =>
    request('/settings', { method: 'PUT', body: JSON.stringify({ authorized_folders: folders }) }),
  browse: (path) => request(`/browse${path ? `?path=${encodeURIComponent(path)}` : ''}`),
  library: (params = {}) => {
    const qs = new URLSearchParams(Object.entries(params).filter(([, v]) => v)).toString()
    return request(`/library${qs ? `?${qs}` : ''}`)
  },
  importVideos: (paths, mode) =>
    request('/library/import', { method: 'POST', body: JSON.stringify({ paths, mode }) }),
  rename: (id, title) =>
    request(`/library/${id}`, { method: 'PATCH', body: JSON.stringify({ title }) }),
  remove: (id) => request(`/library/${id}`, { method: 'DELETE' }),

  // IA y clips
  aiStatus: () => request('/ai/status'),
  saveAiSettings: (s) => request('/ai/settings', { method: 'PUT', body: JSON.stringify(s) }),
  videosOverview: () => request('/videos/overview'),
  video: (id) => request(`/library/${id}`),
  analyze: (id, opts) => request(`/videos/${id}/analyze`, { method: 'POST', body: JSON.stringify(opts) }),
  analysis: (id) => request(`/videos/${id}/analysis`),
  transcript: (id) => request(`/videos/${id}/transcript`),
  clips: (videoId) => request(`/videos/${videoId}/clips`),
  clip: (id) => request(`/clips/${id}`),
  createClip: (data) => request('/clips', { method: 'POST', body: JSON.stringify(data) }),
  updateClip: (id, data) => request(`/clips/${id}`, { method: 'PATCH', body: JSON.stringify(data) }),
  deleteClip: (id) => request(`/clips/${id}`, { method: 'DELETE' }),
  renderClip: (id, opts) => request(`/clips/${id}/render`, { method: 'POST', body: JSON.stringify(opts) }),
  editClip: (id, opts) => request(`/clips/${id}/edit`, { method: 'POST', body: JSON.stringify(opts) }),
  studioProxy: (id) => request(`/studio/projects/${id}/proxy`, { method: 'POST' }),
  job: (id) => request(`/jobs/${id}`),
  jobs: (params = {}) => request(`/jobs?${new URLSearchParams(params)}`),
  cancelJob: (id) => request(`/jobs/${id}/cancel`, { method: 'POST' }),
  renders: () => request('/renders'),
  deleteRender: (id) => request(`/renders/${id}`, { method: 'DELETE' }),

  // Studio
  studioStatus: () => request('/studio/status'),
  studioProjects: () => request('/studio/projects'),
  studioProject: (id) => request(`/studio/projects/${id}`),
  studioCreate: (data) => request('/studio/projects', { method: 'POST', body: JSON.stringify(data) }),
  studioRename: (id, name) => request(`/studio/projects/${id}`, { method: 'PATCH', body: JSON.stringify({ name }) }),
  studioDelete: (id) => request(`/studio/projects/${id}`, { method: 'DELETE' }),
  studioAutoedit: (id, opts) => request(`/studio/projects/${id}/autoedit`, { method: 'POST', body: JSON.stringify(opts) }),
  studioSaveTimeline: (id, timeline, note) =>
    request(`/studio/projects/${id}/timeline`, { method: 'PUT', body: JSON.stringify({ timeline, note }) }),
  studioSaveStyle: (id, style, asDefault = false) =>
    request(`/studio/projects/${id}/style`, { method: 'PUT', body: JSON.stringify({ style, as_default: asDefault }) }),
  studioUndo: (id) => request(`/studio/projects/${id}/undo`, { method: 'POST' }),
  studioRedo: (id) => request(`/studio/projects/${id}/redo`, { method: 'POST' }),
  studioSplit: (id, time) => request(`/studio/projects/${id}/split`, { method: 'POST', body: JSON.stringify({ time }) }),
  studioInstruct: (id, text, llm_model) => request(`/studio/projects/${id}/instruct`, { method: 'POST', body: JSON.stringify({ text, llm_model: llm_model || null }) }),
  studioReference: (id, data) => request(`/studio/projects/${id}/reference`, { method: 'POST', body: JSON.stringify(data) }),
  studioRender: (id, quality) => request(`/studio/projects/${id}/render`, { method: 'POST', body: JSON.stringify({ quality }) }),
  studioRenders: (id) => request(`/studio/projects/${id}/renders`),
  studioJobs: (id) => request(`/studio/projects/${id}/jobs`),
  studioDeleteRender: (id) => request(`/studio/renders/${id}`, { method: 'DELETE' }),
  assets: (kind) => request(`/studio/assets${kind ? `?kind=${kind}` : ''}`),
  scanAssets: (folder, kind) => request('/studio/assets/scan', { method: 'POST', body: JSON.stringify({ folder, kind: kind || null }) }),
  patchAsset: (id, data) => request(`/studio/assets/${id}`, { method: 'PATCH', body: JSON.stringify(data) }),
  deleteAsset: (id) => request(`/studio/assets/${id}`, { method: 'DELETE' }),
}

/** Sube un archivo al Studio con progreso (0-1). */
export function uploadStudioFile(file, onProgress, filename) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest()
    const fd = new FormData()
    fd.append('file', file, filename || file.name)
    xhr.open('POST', '/api/studio/upload')
    xhr.upload.onprogress = (e) => e.lengthComputable && onProgress?.(e.loaded / e.total)
    xhr.onload = () => {
      let data = null
      try { data = JSON.parse(xhr.responseText) } catch { /* sin cuerpo */ }
      if (xhr.status >= 200 && xhr.status < 300) resolve(data)
      else reject(new Error(data?.detail || `Error ${xhr.status}`))
    }
    xhr.onerror = () => reject(new Error('No se pudo subir el archivo'))
    xhr.send(fd)
  })
}

export function fmtTime(sec) {
  if (sec == null) return '—'
  const m = Math.floor(sec / 60)
  const s = (sec % 60).toFixed(1).padStart(4, '0')
  return `${m}:${s}`
}

export function getQuery() {
  const q = window.location.hash.split('?')[1] || ''
  return Object.fromEntries(new URLSearchParams(q))
}

export function fmtDuration(sec) {
  if (sec == null) return '—'
  const s = Math.round(sec)
  const h = Math.floor(s / 3600)
  const m = Math.floor((s % 3600) / 60)
  const r = String(s % 60).padStart(2, '0')
  return h ? `${h}:${String(m).padStart(2, '0')}:${r}` : `${m}:${r}`
}

export function fmtSize(bytes) {
  if (bytes == null) return '—'
  const u = ['B', 'KB', 'MB', 'GB', 'TB']
  let i = 0
  let n = bytes
  while (n >= 1024 && i < u.length - 1) { n /= 1024; i++ }
  return `${n.toFixed(n < 10 && i > 0 ? 1 : 0)} ${u[i]}`
}

export function fmtDate(ts) {
  return new Date(ts * 1000).toLocaleDateString('es-MX', { day: '2-digit', month: 'short', year: 'numeric' })
}
