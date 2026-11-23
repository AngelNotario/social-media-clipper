"""Miniaturas: generar con IA, ver variantes, editar el texto, elegir una para publicar."""
from __future__ import annotations

import uuid
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from .. import db, jobs
from ..thumbs import engine, store

router = APIRouter(prefix="/thumbs", tags=["thumbs"])
jobs.HANDLERS["thumb_generate"] = engine.run_generate

Kind = Literal["clip_render", "studio_render", "project", "video"]


@router.post("/upload")
async def upload(file: UploadFile = File(...)):
    ext = Path(file.filename or "x.jpg").suffix.lower()
    if ext not in (".jpg", ".jpeg", ".png", ".webp"):
        raise HTTPException(400, "Sube una imagen JPG, PNG o WEBP.")
    data = await file.read()
    if len(data) > 15_000_000:
        raise HTTPException(400, "La imagen pesa demasiado (máx. 15 MB).")
    store.UPLOADS.mkdir(parents=True, exist_ok=True)
    name = uuid.uuid4().hex[:12] + ext
    (store.UPLOADS / name).write_bytes(data)
    return {"ref": f"upload:{name}", "url": f"/api/thumbs/uploads/{name}", "name": file.filename}


@router.get("/uploads/{name}")
def get_upload(name: str):
    p = store.UPLOADS / Path(name).name
    if not p.exists():
        raise HTTPException(404, "No encontrado.")
    return FileResponse(p)


@router.get("/sources")
def sources():
    """Proyectos del Studio, exportaciones y videos de la biblioteca, para elegir de qué hacer la miniatura."""
    with db.get_conn() as conn:
        projects = conn.execute("SELECT id, name, updated_at FROM studio_projects ORDER BY updated_at DESC LIMIT 30").fetchall()
        finals = conn.execute("""SELECT r.id, p.name, r.created_at FROM studio_renders r JOIN studio_projects p
                                 ON p.id = r.project_id WHERE r.quality = 'final' ORDER BY r.id DESC LIMIT 20""").fetchall()
        clips = conn.execute("""SELECT r.id, c.title, r.created_at FROM renders r JOIN clips c ON c.id = r.clip_id
                                ORDER BY r.id DESC LIMIT 20""").fetchall()
        vids = conn.execute("SELECT id, title FROM videos ORDER BY id DESC LIMIT 30").fetchall()
    return ([{"kind": "project", "id": r["id"], "title": r["name"], "group": "Proyectos del Studio"} for r in projects]
            + [{"kind": "studio_render", "id": r["id"], "title": r["name"], "group": "Videos exportados del Studio"} for r in finals]
            + [{"kind": "clip_render", "id": r["id"], "title": r["title"], "group": "Clips exportados"} for r in clips]
            + [{"kind": "video", "id": r["id"], "title": r["title"], "group": "Biblioteca"} for r in vids])


class GenReq(BaseModel):
    source_kind: Kind
    source_id: int
    instructions: str = ""
    text: str = ""
    references: list[str] = []   # links de YouTube, URLs de imágenes o "upload:..."
    assets: list[str] = []       # tus recursos (logos, fotos) para usar en la miniatura
    use_web: bool = True
    n: int = 3


@router.post("/generate")
def generate(req: GenReq):
    try:
        engine.resolve(req.source_kind, req.source_id)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(404, str(exc)) from exc
    return jobs.enqueue("thumb_generate", params=req.model_dump())


@router.get("")
def list_thumbs(kind: Kind | None = None, id: int | None = None):
    return store.list_for(kind, id)


@router.get("/{tid}/file")
def thumb_file(tid: int, download: bool = False):
    t = store.get(tid)
    if not t or not Path(t["path"]).exists():
        raise HTTPException(404, "Miniatura no encontrada.")
    return FileResponse(t["path"], media_type="image/jpeg",
                        filename=f"miniatura_{tid}.jpg" if download else None)


class Patch(BaseModel):
    text: str | None = None
    highlight_word: str | None = None
    text_color: str | None = None
    accent_color: str | None = None
    highlight_box: bool | None = None
    layout: Literal["left", "right", "center"] | None = None
    arrow: bool | None = None


@router.patch("/{tid}")
def edit(tid: int, req: Patch):
    t = store.get(tid)
    if not t:
        raise HTTPException(404, "Miniatura no encontrada.")
    patch = {k: v for k, v in req.model_dump().items() if v is not None}
    out = engine.recompose(t, patch)
    store.update(tid, concept={**t["concept"], **patch}, path=str(out))
    return store.get(tid)


@router.post("/{tid}/choose")
def choose(tid: int):
    try:
        store.choose(tid)
    except store.ThumbError as exc:
        raise HTTPException(404, str(exc)) from exc
    return store.get(tid)


@router.delete("/{tid}")
def delete(tid: int):
    with db.get_conn() as conn:
        conn.execute("DELETE FROM thumbnails WHERE id = ?", (tid,))
    return {"ok": True}
