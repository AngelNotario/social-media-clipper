import Icon from './Icon.jsx'
import { fmtDuration } from '../api.js'

export default function VideoCard({ video, onClick }) {
  return (
    <button className="vcard" onClick={onClick}>
      <div className="vcard-thumb">
        {video.thumbnail_url
          ? <img src={video.thumbnail_url} alt="" loading="lazy" />
          : <div className="vcard-nothumb"><Icon name="film" size={28} /></div>}
        <span className="badge badge-dark vcard-dur">{fmtDuration(video.duration)}</span>
        {video.orientation && <span className={`badge vcard-or or-${video.orientation}`}>{video.orientation}</span>}
        {video.available === false && <span className="badge badge-warn vcard-missing">No encontrado</span>}
        <div className="vcard-play"><Icon name="play" size={26} /></div>
      </div>
      <div className="vcard-body">
        <div className="vcard-title" title={video.title}>{video.title}</div>
        <div className="vcard-sub">{video.width && `${video.width}×${video.height}`}</div>
      </div>
    </button>
  )
}
