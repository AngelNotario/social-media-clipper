"""Biblioteca de videos: importar, listar, reproducir, renombrar y quitar."""
from __future__ import annotations

import shutil
import uuid
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field

from .. import config, db, media, security

router = APIRouter(prefix="/library", tags=["library"])

CHUNK = 1024 * 1024


def _row_to_dict(row) -> dict:
    d = dict(row)
    d["has_audio"] = bool(d["has_audio"])
    d["available"] = Path(d["stored_path"]).exists()
    d["thumbnail_url"] = f"/api/library/{d['id']}/thumbnail" if d.get("thumbnail") else None
    d["stream_url"] = f"/api/library/{d['id']}/stream"
    w, h = d.get("width"), d.get("height")
    d["orientation"] = (
        None if not (w and h) else "vertical" if h > w else "horizontal" if w > h else "cuadrado"
    )
    return d


def _get_video(video_id: int) -> dict:
    with db.get_conn() as conn:
        row = conn.execute("SELECT * FROM videos WHERE id = ?", (video_id,)).fetchone()
    if not row:
        raise HTTPException(404, "Video no encontrado en la biblioteca.")
    return _row_to_dict(row)


class ImportRequest(BaseModel):
    paths: list[str] = Field(min_length=1, max_length=200)
    mode: Literal["reference", "copy"] = "reference"


def _import_one(raw: str, mode: str) -> dict:
    try:
        src = security.require_authorized(raw)
    except HTTPException as exc:
        return {"path": raw, "ok": False, "error": exc.detail}
    if not src.is_file():
        return {"path": raw, "ok": False, "error": "No es un archivo."}
    if src.suffix.lower() not in config.VIDEO_EXTS:
        return {"path": raw, "ok": False, "error": "Extensión de video no compatible."}

    with db.get_conn() as conn:
        existing = conn.execute(
            "SELECT id FROM videos WHERE original_path = ?", (str(src),)
        ).fetchone()
    if existing:
        return {"path": raw, "ok": False, "error": "Ya está en la biblioteca.",
                "video_id": existing["id"]}

    try:
        meta = media.probe(src)
    except media.MediaError as exc:
        return {"path": raw, "ok": False, "error": str(exc)}

    stored = src
    if mode == "copy":
        stored = config.MEDIA_DIR / f"{uuid.uuid4().hex[:8]}_{src.name}"
        try:
            shutil.copy2(src, stored)
        except OSError as exc:
            return {"path": raw, "ok": False, "error": f"No se pudo copiar: {exc}"}

    with db.get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO videos (title, original_path, stored_path, storage_mode,
                   size_bytes, duration, width, height, fps, video_codec, audio_codec, has_audio)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (src.stem, str(src), str(stored), mode, meta["size_bytes"], meta["duration"],
             meta["width"], meta["height"], meta["fps"], meta["video_codec"],
             meta["audio_codec"], int(meta["has_audio"])),
        )
        vid = cur.lastrowid

    thumb = config.THUMBS_DIR / f"{vid}.jpg"
    if media.make_thumbnail(stored, thumb, meta["duration"]):
        with db.get_conn() as conn:
            conn.execute("UPDATE videos SET thumbnail = ? WHERE id = ?", (thumb.name, vid))

    return {"path": raw, "ok": True, "video": _get_video(vid)}


@router.post("/import")
def import_videos(req: ImportRequest):
    if not media.ffmpeg_available():
        raise HTTPException(503, "FFmpeg no está disponible: instálalo y reinicia la app.")
    results = [_import_one(p, req.mode) for p in req.paths]
    return {
        "imported": sum(1 for r in results if r["ok"]),
        "failed": sum(1 for r in results if not r["ok"]),
        "results": results,
    }


@router.get("")
def list_videos(
    q: str | None = None,
    orientation: Literal["vertical", "horizontal", "cuadrado"] | None = None,
    sort: Literal["recent", "title", "duration"] = "recent",
    include_missing: bool = False,
):
    from .. import library_sync
    sync = library_sync.kick()  # agrega solos los videos nuevos de tus carpetas autorizadas
    order = {"recent": "id DESC", "title": "title COLLATE NOCASE", "duration": "duration DESC"}[sort]
    sql, params = "SELECT * FROM videos", []
    if q:
        sql += " WHERE title LIKE ?"
        params.append(f"%{q}%")
    with db.get_conn() as conn:
        rows = conn.execute(f"{sql} ORDER BY {order}", params).fetchall()
    items = [_row_to_dict(r) for r in rows]
    if not include_missing:  # lo que borraste o moviste de tu PC ya no se muestra
        items = [i for i in items if i["available"]]
    if orientation:
        items = [i for i in items if i["orientation"] == orientation]
    return {"items": items, "total": len(items), "folders": [str(r) for r in security.authorized_roots()], **sync}


@router.get("/{video_id}")
def get_video(video_id: int):
    return _get_video(video_id)


class UpdateRequest(BaseModel):
    title: str = Field(min_length=1, max_length=200)


@router.patch("/{video_id}")
def update_video(video_id: int, req: UpdateRequest):
    _get_video(video_id)
    with db.get_conn() as conn:
        conn.execute("UPDATE videos SET title = ? WHERE id = ?", (req.title.strip(), video_id))
    return _get_video(video_id)


@router.delete("/{video_id}")
def remove_video(video_id: int):
    """Quita el video de la biblioteca. Nunca borra el archivo original del usuario;
    solo borra la copia interna si el video se importó en modo 'copy'."""
    v = _get_video(video_id)
    if v["storage_mode"] == "copy":
        stored = Path(v["stored_path"])
        if security.is_within(stored, config.MEDIA_DIR) and stored.exists():
            stored.unlink()
    if v.get("thumbnail"):
        (config.THUMBS_DIR / v["thumbnail"]).unlink(missing_ok=True)
    from .ai import cleanup_video_exports
    cleanup_video_exports(video_id)
    with db.get_conn() as conn:
        conn.execute("DELETE FROM videos WHERE id = ?", (video_id,))
    return {"ok": True}


@router.get("/{video_id}/thumbnail")
def thumbnail(video_id: int):
    v = _get_video(video_id)
    if not v.get("thumbnail"):
        raise HTTPException(404, "Sin miniatura.")
    path = config.THUMBS_DIR / v["thumbnail"]
    if not path.exists():
        raise HTTPException(404, "Sin miniatura.")
    return FileResponse(path, media_type="image/jpeg")


def _parse_range(header: str, size: int) -> tuple[int, int] | None:
    if not header.startswith("bytes="):
        return None
    spec = header[6:].split(",")[0].strip()
    start_s, _, end_s = spec.partition("-")
    try:
        if start_s == "":  # bytes=-N (últimos N bytes)
            n = int(end_s)
            if n <= 0:
                return None
            start, end = max(size - n, 0), size - 1
        else:
            start = int(start_s)
            end = int(end_s) if end_s else size - 1
    except ValueError:
        return None
    end = min(end, size - 1)
    if start > end or start >= size:
        return None
    return start, end


@router.get("/{video_id}/stream")
def stream(video_id: int, request: Request):
    v = _get_video(video_id)
    path = Path(v["stored_path"])
    if not path.exists():
        raise HTTPException(410, "El archivo ya no existe en su ubicación original.")
    size = path.stat().st_size
    mime = media.guess_mime(path)
    range_header = request.headers.get("range")

    if not range_header:
        return FileResponse(path, media_type=mime, headers={"Accept-Ranges": "bytes"})

    rng = _parse_range(range_header, size)
    if rng is None:
        raise HTTPException(416, "Rango inválido.", headers={"Content-Range": f"bytes */{size}"})
    start, end = rng
    length = end - start + 1

    def iterfile():
        with open(path, "rb") as f:
            f.seek(start)
            remaining = length
            while remaining > 0:
                chunk = f.read(min(CHUNK, remaining))
                if not chunk:
                    break
                remaining -= len(chunk)
                yield chunk

    return StreamingResponse(
        iterfile(),
        status_code=206,
        media_type=mime,
        headers={
            "Content-Range": f"bytes {start}-{end}/{size}",
            "Accept-Ranges": "bytes",
            "Content-Length": str(length),
        },
    )
