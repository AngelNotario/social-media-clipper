"""Videos de referencia desde enlaces (YouTube, TikTok, Instagram, X, Twitch, Facebook…) con yt-dlp.

Se descargan en data/references (máx. 1080p) solo para que la app estudie su estilo de edición.
Cada enlace se descarga una vez (caché por URL). Úsalo con videos que tengas permiso de descargar.
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
from pathlib import Path
from typing import Callable

from .. import config

REF_DIR = config.DATA_DIR / "references"
MAX_DURATION = 30 * 60  # más largo: solo se descargan los primeros minutos
LONG_CLIP_S = 10 * 60  # de un video largo basta el inicio para aprender el estilo
URL_RE = re.compile(r"^https?://[^\s]+$", re.I)


class RefError(RuntimeError):
    pass


def parse_urls(text: str | list[str] | None) -> list[str]:
    items = text if isinstance(text, list) else re.split(r"[\s,]+", text or "")
    out = []
    for u in items:
        u = (u or "").strip().strip("<>\"'")
        if URL_RE.match(u) and u not in out:
            out.append(u)
    return out


def _key(url: str) -> str:
    return hashlib.sha1(url.strip().encode()).hexdigest()[:14]


def cached(url: str) -> dict | None:
    meta = REF_DIR / f"{_key(url)}.json"
    if meta.exists():
        d = json.loads(meta.read_text(encoding="utf-8"))
        if Path(d.get("path", "")).exists():
            return d
    return None


COOKIE_FILE = REF_DIR / "cookies.txt"
BROWSERS = ("firefox", "chrome", "edge", "brave", "opera", "vivaldi", "chromium")
AUTH_HINTS = ("sign in", "login", "log in", "cookies", "confirm your age", "age-restricted", "private video",
              "members-only", "inappropriate")


def cookie_source() -> dict:
    """Sesión para videos con restricción de edad o privados: archivo cookies.txt o cookies del navegador."""
    from .. import db
    if COOKIE_FILE.exists():
        return {"cookiefile": str(COOKIE_FILE)}
    b = (db.get_setting("ytdlp_cookies_browser") or "").strip().lower()
    if b in BROWSERS:
        return {"cookiesfrombrowser": (b,)}
    return {}


def _trim(path: Path, seconds: int) -> Path:
    """Se queda con los primeros minutos (copia directa de pistas, tarda segundos)."""
    import subprocess
    ff = shutil.which("ffmpeg")
    if not ff:
        return path
    out = path.with_name(path.stem + ".cut.mp4")
    r = subprocess.run([ff, "-y", "-v", "error", "-i", str(path), "-t", str(seconds), "-map", "0:v:0", "-map", "0:a:0?",
                        "-c", "copy", "-movflags", "+faststart", str(out)], capture_output=True, timeout=600)
    if r.returncode != 0 or not out.exists() or out.stat().st_size < 1000:
        out.unlink(missing_ok=True)
        return path
    final = path.with_suffix(".mp4")
    path.unlink(missing_ok=True)
    out.replace(final)
    return final


def _needs_auth(msg: str) -> bool:
    m = msg.lower()
    return any(h in m for h in AUTH_HINTS)


def download(url: str, progress: Callable[[float, str], None] = lambda p, s: None) -> dict:
    """Descarga (o reutiliza) el video. Devuelve path, título, autor, duración, plataforma y URL."""
    if not URL_RE.match(url or ""):
        raise RefError(f"Enlace no válido: {url}")
    hit = cached(url)
    if hit:
        return hit
    try:
        import yt_dlp
    except ImportError as exc:
        raise RefError("yt-dlp no está instalado en el entorno de la app.") from exc
    REF_DIR.mkdir(parents=True, exist_ok=True)
    key = _key(url)

    def hook(d):
        if d.get("status") == "downloading":
            tot = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            if tot:
                progress(min(d.get("downloaded_bytes", 0) / tot, 1.0), "Descargando referencia")
        elif d.get("status") == "finished":
            progress(1.0, "Uniendo audio y video")

    opts = {
        "outtmpl": str(REF_DIR / f"{key}.%(ext)s"),
        "format": "bv*[height<=1080][ext=mp4]+ba[ext=m4a]/bv*[height<=1080]+ba/b[height<=1080]/b",
        "merge_output_format": "mp4",
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "retries": 3,
        "concurrent_fragment_downloads": 4,
        "progress_hooks": [hook],
        "restrictfilenames": True,
    }
    ff = shutil.which("ffmpeg")
    if ff:
        opts["ffmpeg_location"] = str(Path(ff).parent)
    node = shutil.which("node")
    if node:  # YouTube necesita un intérprete de JavaScript; usamos el Node que ya tienes
        opts["js_runtimes"] = {"node": {"path": node}}
    def run(o):
        with yt_dlp.YoutubeDL(o) as ydl:
            meta = ydl.extract_info(url, download=False)
            if meta and (meta.get("duration") or 0) > MAX_DURATION:
                # Descargar un tramo directo desde YouTube pasa por ffmpeg y es lentísimo; es más rápido bajar el video
                # completo en 720p con el descargador normal y recortar aquí los primeros minutos (sin recodificar).
                progress(0.0, f"Video de {meta['duration'] / 60:.0f} min: uso solo los primeros {LONG_CLIP_S // 60}")
                ydl.params["format"] = "bv*[height<=720][ext=mp4]+ba[ext=m4a]/bv*[height<=720]+ba/b[height<=720]/b"
                ydl.format_selector = ydl.build_format_selector(ydl.params["format"])
                meta["_partial"] = True
            return ydl.process_ie_result(meta, download=True) if meta else None

    def clean(exc) -> str:
        return re.sub(r"\x1b\[[0-9;]*m", "", str(exc)).replace("ERROR: ", "").strip()

    try:
        info = run(opts)  # primero sin sesión
    except Exception as exc:  # noqa: BLE001 — yt-dlp lanza muchos tipos
        msg = clean(exc)
        if not _needs_auth(msg):
            raise RefError(f"No se pudo descargar {url}: {msg[:300]}") from exc
        cookies = cookie_source()
        if not cookies:
            raise RefError(f"No se pudo descargar {url}: el video pide iniciar sesión (restricción de edad o privado). "
                           "Configura tu sesión en «Videos con restricción de edad» o súbelo como archivo.") from exc
        progress(0.0, "Reintentando con tu sesión")
        try:
            info = run({**opts, **cookies})
        except Exception as exc2:  # noqa: BLE001
            m2 = clean(exc2)
            hint = ""
            if "cookiesfrombrowser" in cookies:
                hint = (" No pude leer las cookies del navegador: Chrome y Edge las bloquean mientras están abiertos "
                        "o las cifran. Usa Firefox, o exporta un archivo cookies.txt y súbelo.")
            elif _needs_auth(m2):
                hint = " Tu cookies.txt parece vencido o de otra cuenta: vuelve a exportarlo con la sesión iniciada."
            raise RefError(f"No se pudo descargar {url}: {m2[:240]}.{hint}") from exc2
    if info is None:
        raise RefError(f"No se pudo descargar {url}: la plataforma no devolvió el video.")
    files = sorted(REF_DIR.glob(f"{key}.*"), key=lambda p: p.stat().st_size, reverse=True)
    files = [f for f in files if f.suffix.lower() in (".mp4", ".mkv", ".webm", ".mov")]
    if not files:
        raise RefError(f"La descarga de {url} no dejó ningún video (puede ser un directo o un formato no soportado).")
    if info.get("_partial"):
        files[0] = _trim(files[0], LONG_CLIP_S)
    d = {"path": str(files[0]), "url": url, "title": info.get("title") or url,
         "uploader": info.get("uploader") or info.get("channel") or "", "duration": info.get("duration"),
         "platform": info.get("extractor_key") or info.get("extractor") or "", "id": info.get("id"),
         "partial": bool(info.get("_partial"))}
    (REF_DIR / f"{key}.json").write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    return d
