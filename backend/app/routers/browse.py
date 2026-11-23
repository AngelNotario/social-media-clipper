"""Explorador de archivos locales limitado a las carpetas autorizadas."""
from __future__ import annotations

import hashlib
import os
import threading
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse

from .. import config, db, media, security

router = APIRouter(prefix="/browse", tags=["browse"])


def _imported_map() -> dict[str, int]:
    with db.get_conn() as conn:
        rows = conn.execute("SELECT id, original_path FROM videos").fetchall()
    return {os.path.normcase(r["original_path"]): r["id"] for r in rows}


@router.get("")
def browse(path: str | None = Query(default=None)):
    roots = security.authorized_roots()

    if not path:
        return {
            "path": None,
            "parent": None,
            "roots": [
                {"name": r.name or str(r), "path": str(r), "exists": r.is_dir()}
                for r in roots
            ],
            "dirs": [],
            "files": [],
        }

    target = security.require_authorized(path)
    if not target.is_dir():
        raise HTTPException(400, "La ruta no es una carpeta.")

    root = security.root_for(target)
    parent = None
    if root is not None and security._norm(target) != security._norm(root):
        parent = str(target.parent)

    imported = _imported_map()
    dirs, files = [], []
    try:
        entries = list(os.scandir(target))
    except PermissionError:
        raise HTTPException(403, "Windows no permite leer esta carpeta.")

    for e in entries:
        if e.name.startswith((".", "$")):
            continue
        try:
            if e.is_dir(follow_symlinks=False):
                dirs.append({"name": e.name, "path": str(Path(e.path))})
            elif e.is_file() and Path(e.name).suffix.lower() in config.VIDEO_EXTS:
                st = e.stat()
                full = str(Path(e.path).resolve())
                files.append({
                    "name": e.name,
                    "path": full,
                    "size_bytes": st.st_size,
                    "modified": st.st_mtime,
                    "imported_id": imported.get(os.path.normcase(full)),
                })
        except OSError:
            continue

    dirs.sort(key=lambda d: d["name"].lower())
    files.sort(key=lambda f: f["name"].lower())
    return {
        "path": str(target),
        "root": str(root) if root else None,
        "parent": parent,
        "roots": [{"name": r.name or str(r), "path": str(r), "exists": r.is_dir()} for r in roots],
        "dirs": dirs,
        "files": files,
    }


# ---- Vista previa: miniatura y reproducción sin importar ----
_THUMB_DIR = config.THUMBS_DIR / "browse"
_ffmpeg_slots = threading.Semaphore(3)  # no lanzar 20 ffmpeg a la vez al abrir una carpeta


def _video_file(path: str) -> Path:
    target = security.require_authorized(path)
    if not target.is_file() or target.suffix.lower() not in config.VIDEO_EXTS:
        raise HTTPException(404, "No es un video disponible.")
    return target


@router.get("/thumb")
def thumb(path: str = Query(...)):
    src = _video_file(path)
    st = src.stat()
    key = hashlib.sha1(f"{os.path.normcase(str(src))}|{st.st_size}|{int(st.st_mtime)}".encode()).hexdigest()[:20]
    dest = _THUMB_DIR / f"{key}.jpg"
    if not dest.exists():
        with _ffmpeg_slots:
            if not dest.exists():
                try:
                    duration = media.probe(src).get("duration")
                except Exception:
                    duration = None
                if not media.make_thumbnail(src, dest, duration):
                    raise HTTPException(422, "No se pudo sacar una miniatura de este video.")
    return FileResponse(dest, media_type="image/jpeg", headers={"Cache-Control": "max-age=86400"})


@router.get("/stream")
def stream(path: str = Query(...)):
    src = _video_file(path)
    # FileResponse atiende Range: se puede adelantar en videos de varios GB sin leerlos completos
    return FileResponse(src, media_type=media.guess_mime(src))
