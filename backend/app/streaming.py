"""Servir archivos con soporte de rangos (necesario para que el navegador pueda adelantar el video)."""
from __future__ import annotations

from pathlib import Path

from fastapi import HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse

from .routers.library import CHUNK, _parse_range


def serve_file(path: Path, request: Request, mime: str, download: bool = False):
    if not path.exists():
        raise HTTPException(410, "El archivo ya no existe.")
    if download:
        return FileResponse(path, media_type=mime, filename=path.name)
    size = path.stat().st_size
    rh = request.headers.get("range")
    if not rh:
        return FileResponse(path, media_type=mime, headers={"Accept-Ranges": "bytes"})
    rng = _parse_range(rh, size)
    if rng is None:
        raise HTTPException(416, "Rango inválido.", headers={"Content-Range": f"bytes */{size}"})
    start, end = rng

    def it():
        with open(path, "rb") as f:
            f.seek(start)
            left = end - start + 1
            while left > 0:
                chunk = f.read(min(CHUNK, left))
                if not chunk:
                    break
                left -= len(chunk)
                yield chunk

    return StreamingResponse(it(), status_code=206, media_type=mime, headers={
        "Content-Range": f"bytes {start}-{end}/{size}", "Accept-Ranges": "bytes",
        "Content-Length": str(end - start + 1)})
