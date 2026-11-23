import Icon from './Icon.jsx'

const NAV = [
  { id: 'dashboard', label: 'Dashboard', icon: 'grid' },
  { id: 'library', label: 'Biblioteca', icon: 'film' },
  { id: 'explorer', label: 'Explorar archivos', icon: 'folder' },
  { id: 'studio', label: 'Studio (original)', icon: 'scissors' },
  { id: 'stream', label: 'Stream', icon: 'play' },
  { id: 'clips', label: 'Clips con IA', icon: 'spark' },
  { id: 'exports', label: 'Exportaciones', icon: 'upload' },
  { id: 'thumbs', label: 'Miniaturas', icon: 'spark' },
  { id: 'resources', label: 'Recursos', icon: 'search' },
  { id: 'publish', label: 'Publicar en redes', icon: 'send' },
  { id: 'settings', label: 'Configuración', icon: 'gear' },
]

// Módulos del roadmap que todavía NO están implementados (se muestran deshabilitados).
const SOON = [
  { label: 'Content Intelligence', icon: 'search' },
  { label: 'Canales', icon: 'grid' },
]

export default function Sidebar({ page, navigate }) {
  return (
    <aside className="sidebar">
      <div className="brand">
        <div className="brand-logo"><Icon name="play" /></div>
        <div>
          <div className="brand-name">SOCIAL MEDIA</div>
          <div className="brand-sub">CLIPPER</div>
        </div>
      </div>

      <nav className="nav">
        {NAV.map((n) => (
          <button
            key={n.id}
            className={`nav-item ${page === n.id ? 'active' : ''}`}
            onClick={() => navigate(n.id)}
          >
            <Icon name={n.icon} />
            <span>{n.label}</span>
          </button>
        ))}
      </nav>

      <div className="nav-section">Próximamente</div>
      <nav className="nav">
        {SOON.map((n) => (
          <div key={n.label} className="nav-item disabled" title="Aún no implementado">
            <Icon name={n.icon} />
            <span>{n.label}</span>
          </div>
        ))}
      </nav>

      <div className="sidebar-foot">v0.3 · Studio</div>
    </aside>
  )
}
