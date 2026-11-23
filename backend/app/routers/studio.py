"""API del Studio: proyectos con tu propio material, timeline, IA editora, recursos y render."""
from __future__ import annotations

import json
import subprocess
import uuid
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from pydantic import BaseModel, Field

from .. import config, db, jobs, media, research, security
from ..ai import llm
from ..streaming import serve_file
from ..studio import agent, assets, autoedit, footage, learn, projects, render, topic, voice
from ..studio import style as style_mod
from ..studio import timeline as tlm
from ..studio.probe import ProbeError, probe_any

router = APIRouter(prefix="/studio", tags=["studio"])

MAX_UPLOAD = 4 * 1024 ** 3  # 4 GB


def _project(pid: int) -> dict:
    p = projects.get(pid)
    if not p:
        raise HTTPException(404, "Proyecto no encontrado.")
    return p


# ---------- estado ----------

@router.get("/status")
def status():
    assets.ensure_starter_pack()
    assets.ensure_project_media()
    counts = {k: len([a for a in assets.list_assets(k) if a["available"]]) for k in ("music", "sfx", "broll")}
    cfg = {"url": db.get_setting("ollama_url"), "model": db.get_setting("ollama_model")}
    ol = llm.status(cfg["url"])
    vm = footage.vision_model()
    fo = footage.overview()
    return {"assets": counts, "llm_ready": ol["available"] and cfg["model"] in ol["models"],
            "llm_model": ("llama.cpp · Qwen3.6 35B" if ol.get("backend") == "llamacpp" else cfg["model"]),
            "llm_backend": ol.get("backend", "ollama"), "default_style": style_mod.DEFAULT_STYLE,
            "asset_folders": db.get_setting("studio_asset_folders", []),
            "vision_model": vm, "vision_ready": ol["available"] and vm in ol["models"],
            "tts_ready": voice.tts_available(), "voice_profile": voice.profile(),
            "earshot_ready": learn.earshot_python() is not None, "ytdlp_ready": _has_ytdlp(),
            "emotions": {k: v["label"] for k, v in voice.EMOTIONS.items()},
            "footage": {"videos": len(fo), "indexed": sum(1 for f in fo if f["status"] == "done"),
                        "shots": sum(f["shots"] or 0 for f in fo)},
            "transitions": render.TRANSITIONS}


# ---------- subidas y grabaciones ----------

@router.post("/upload")
async def upload(file: UploadFile = File(...)):
    ext = Path(file.filename or "").suffix.lower() or ".webm"
    if ext not in config.VIDEO_EXTS | config.AUDIO_EXTS | {".webm"}:
        raise HTTPException(400, "Formato no compatible. Sube un video o un audio.")
    dest = config.STUDIO_MEDIA_DIR / f"{uuid.uuid4().hex[:10]}{ext}"
    size = 0
    with open(dest, "wb") as out:
        while chunk := await file.read(1024 * 1024):
            size += len(chunk)
            if size > MAX_UPLOAD:
                out.close()
                dest.unlink(missing_ok=True)
                raise HTTPException(413, "Archivo demasiado grande (máx. 4 GB).")
            out.write(chunk)
    try:
        meta = probe_any(dest)
    except ProbeError as exc:
        dest.unlink(missing_ok=True)
        raise HTTPException(400, str(exc))
    if not meta["has_video"] and not meta["has_audio"]:
        dest.unlink(missing_ok=True)
        raise HTTPException(400, "El archivo no tiene video ni audio.")
    # Las grabaciones del navegador (webm) a veces no traen duración: se remultiplexan a un archivo indexado
    if not meta["duration"]:
        fixed = dest.with_suffix(".mkv")
        r = subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(dest), "-c", "copy", str(fixed)],
                                 capture_output=True)
        if r.returncode == 0 and fixed.exists():
            dest.unlink(missing_ok=True)
            dest = fixed
            meta = probe_any(dest)
    return {"upload_id": dest.name, "name": file.filename, "meta": meta}


# ---------- proyectos ----------

class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    format: Literal["vertical", "horizontal", "square"] = "vertical"
    source_type: Literal["library", "upload"]
    video_id: int | None = None
    upload_id: str | None = None
    auto_edit: bool = True
    use_ai: bool = True
    style: dict | None = None
    brief: dict | None = None  # tipo de video, estilo de edición, subtítulos, adjuntos, instrucciones


@router.get("/projects")
def list_projects():
    with db.get_conn() as conn:
        rows = conn.execute("SELECT id FROM studio_projects ORDER BY updated_at DESC").fetchall()
    out = []
    for r in rows:
        p = projects.get(r["id"])
        pub = projects.public(p)
        pub.pop("timeline", None)
        pub["duration"] = p["timeline"]["duration"]
        out.append(pub)
    return out


@router.post("/projects")
def create_project(req: ProjectCreate):
    if req.source_type == "library":
        with db.get_conn() as conn:
            v = conn.execute("SELECT * FROM videos WHERE id = ?", (req.video_id or 0,)).fetchone()
        if not v:
            raise HTTPException(404, "Video no encontrado en la biblioteca.")
        path = Path(v["stored_path"])
    else:
        if not req.upload_id or "/" in req.upload_id or "\\" in req.upload_id:
            raise HTTPException(400, "Subida inválida.")
        path = config.STUDIO_MEDIA_DIR / req.upload_id
    if not path.exists():
        raise HTTPException(404, "El archivo no existe.")
    try:
        meta = probe_any(path)
    except ProbeError as exc:
        raise HTTPException(400, str(exc))
    if not meta["duration"]:
        raise HTTPException(400, "No se pudo determinar la duración del archivo.")
    kind = "video" if meta["has_video"] else "audio"
    tl = tlm.empty(req.format)
    tl["tracks"]["video"].append({"id": tlm.nid("v"), "src_in": 0.0, "src_out": meta["duration"],
                                  "start": 0.0, "zoom": 1.0, "crop_x": 0.5})
    base_style = req.style or db.get_setting("studio_default_style") or {}
    with db.get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO studio_projects (name, format, source_path, source_kind, source_meta, timeline, style) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (req.name.strip(), req.format, str(path), kind, json.dumps(meta), json.dumps(tl),
             json.dumps(style_mod.clean_style(base_style))))
        pid = cur.lastrowid
    projects.save(pid, note="Proyecto creado")
    if req.brief:
        from ..studio import brief as brief_mod
        brief_mod.save(pid, req.brief)
    job = None
    if req.auto_edit:
        job = jobs.enqueue("studio_autoedit", params={"project_id": pid, "use_ai": req.use_ai})
    return {"project": projects.public(projects.get(pid)), "job": job}


@router.get("/projects/{pid}")
def get_project(pid: int):
    return projects.public(_project(pid))


class ProjectPatch(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=120)


@router.patch("/projects/{pid}")
def patch_project(pid: int, req: ProjectPatch):
    _project(pid)
    if req.name:
        with db.get_conn() as conn:
            conn.execute("UPDATE studio_projects SET name = ? WHERE id = ?", (req.name.strip(), pid))
    return projects.public(projects.get(pid))


@router.delete("/projects/{pid}")
def delete_project(pid: int):
    p = _project(pid)
    with db.get_conn() as conn:
        for r in conn.execute("SELECT path FROM studio_renders WHERE project_id = ?", (pid,)).fetchall():
            rp = Path(r["path"])
            if security.is_within(rp, config.STUDIO_RENDERS_DIR):
                try:
                    rp.unlink(missing_ok=True)
                except OSError:  # en uso (Windows): se omite
                    pass
        conn.execute("DELETE FROM studio_projects WHERE id = ?", (pid,))
        others = conn.execute("SELECT COUNT(*) AS n FROM studio_projects WHERE source_path = ?",
                              (p["source_path"],)).fetchone()["n"]
    src = Path(p["source_path"])  # solo se borran grabaciones/subidas propias de la app, nunca la biblioteca
    if others == 0 and security.is_within(src, config.STUDIO_MEDIA_DIR):
        try:
            src.unlink(missing_ok=True)
        except OSError:  # en uso (Windows): se omite
            pass
    return {"ok": True}


@router.get("/projects/{pid}/source")
def project_source(pid: int, request: Request):
    p = _project(pid)
    path = Path(p["source_path"])
    return serve_file(path, request, media.guess_mime(path) if p["source_kind"] == "video" else "audio/mpeg")


@router.get("/projects/{pid}/export/resolve")
def export_resolve(pid: int):
    """Proyecto para DaVinci Resolve: FCPXML (toma con cortes, recursos, textos, música y efectos) + subtítulos SRT."""
    from fastapi.responses import Response
    from urllib.parse import quote
    from ..studio import resolve_export
    p = _project(pid)
    if not p["timeline"]["tracks"]["video"]:
        raise HTTPException(400, "El proyecto aún no tiene tomas en el timeline.")
    data, name = resolve_export.build_zip(p)
    fname = f"{name} - DaVinci Resolve.zip"
    return Response(data, media_type="application/zip", headers={
        "Content-Disposition": f"attachment; filename*=UTF-8''{quote(fname)}"})


@router.post("/projects/{pid}/proxy")
def project_proxy(pid: int):
    """Copia ligera (H.264 720p) del tramo que usa la edición, para la vista en vivo cuando el navegador no
    puede leer el original (p. ej. HEVC de OBS). Se guarda en caché."""
    import hashlib
    import subprocess
    p = _project(pid)
    src = Path(p["source_path"])
    if not src.exists():
        raise HTTPException(404, "El archivo de tu toma ya no está disponible.")
    segs = p["timeline"]["tracks"]["video"]
    a = max(0.0, min((s["src_in"] for s in segs), default=0.0) - 2)
    b = max((s["src_out"] for s in segs), default=p["source_meta"].get("duration") or 0) + 2
    st = src.stat()
    sig = hashlib.sha1(f"v3|{src.resolve()}|{st.st_size}|{int(st.st_mtime)}|{a:.1f}|{b:.1f}".encode()).hexdigest()[:16]
    out = config.DATA_DIR / "cache" / "proxy" / f"{sig}.mp4"
    if not out.exists():
        out.parent.mkdir(parents=True, exist_ok=True)
        tmp = out.with_suffix(".tmp.mp4")
        from ..ai import gpu
        # Un cuadro clave cada 0.5 s (15 cuadros a 30 fps): la vista en vivo salta entre tomas cortas al instante.
        # Antes el cuadro clave venía cada ~4 s y en tomas de menos de 1 s se veían pedazos repetidos.
        venc = ["-c:v", "h264_nvenc", "-preset", "p4", "-cq", "23", "-g", "15", "-bf", "0"] if gpu.nvenc_available() else \
            ["-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-g", "15", "-bf", "0"]
        cmd = ["ffmpeg", "-y", "-v", "error", "-ss", f"{a:.2f}", "-t", f"{b - a:.2f}", "-i", str(src),
               "-vf", "scale=-2:720:flags=lanczos,fps=30", *venc, "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k",
               "-movflags", "+faststart", str(tmp)]
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=3600)
        if r.returncode != 0 or not tmp.exists():
            tmp.unlink(missing_ok=True)
            raise HTTPException(500, f"No se pudo preparar la vista en vivo: {r.stderr.strip()[-200:]}")
        tmp.replace(out)
    return {"url": f"/api/studio/proxy/{out.name}", "offset": round(a, 3)}


@router.get("/proxy/{name}")
def proxy_file(name: str, request: Request):
    if not name.endswith(".mp4") or "/" in name or "\\" in name or ".." in name:
        raise HTTPException(400, "Nombre inválido.")
    path = config.DATA_DIR / "cache" / "proxy" / name
    if not path.exists():
        raise HTTPException(404, "No existe.")
    return serve_file(path, request, "video/mp4")


@router.get("/projects/{pid}/transcript")
def project_transcript(pid: int):
    p = _project(pid)
    if not p["transcript"]:
        raise HTTPException(404, "Aún no hay transcripción.")
    return p["transcript"]


@router.post("/projects/{pid}/sound-judge")
def sound_judge_project(pid: int):
    """Juez de sonido sobre la edición actual. Si el último render corresponde a esta edición, también lo escucha."""
    from ..studio import agent, brief as brief_mod, projects, sound_judge
    from ..studio import timeline as tlm
    p = _project(pid)
    tl = p["timeline"]
    if not tl["tracks"]["sfx"]:
        return {"report": {"before": 0, "after": 0, "changes": [], "summary": "Esta edición no tiene efectos de sonido.",
                           "compare": [], "score": 10}, "project": None}
    b = brief_mod.infer(p)
    h = tlm.thash(tl, p["style"])
    with db.get_conn() as conn:
        r = conn.execute("SELECT path FROM studio_renders WHERE project_id = ? AND timeline_hash = ? ORDER BY id DESC LIMIT 1",
                         (pid, h)).fetchone()
    rep = sound_judge.judge(tl, p["style"], video_type=b.get("video_type") or "", edit_style=b.get("edit_style") or "",
                            style_id=b.get("learned_style_id"), render_path=r["path"] if r else None,
                            use_llm=agent.llm_ready())
    changed = bool(rep["changes"])
    out = projects.save(pid, tl, note="Juez de sonido") if changed else None
    return {"report": rep, "project": out}


@router.get("/projects/{pid}/style-score")
def style_score(pid: int):
    """Qué tanto se parece la edición actual al estilo elegido (referencia aprendida o preset del tipo de video)."""
    from ..studio import brief as brief_mod, style_judge
    p = _project(pid)
    b = brief_mod.infer(p)
    t = style_judge.targets_for(b.get("learned_style_id"), b.get("video_type") or "long", b.get("edit_style") or "informativo")
    sim = style_judge.similarity(t, style_judge.edit_metrics(p["timeline"]))
    return {**sim, "goal": 80, "reference": t.get("name"), "sources": t.get("sources")}


@router.get("/projects/{pid}/brief")
def get_brief(pid: int):
    from ..studio import brief as brief_mod
    p = _project(pid)
    return {"saved": brief_mod.normalize((p["source_meta"] or {}).get("brief")), "effective": brief_mod.infer(p),
            "edit_styles": list(brief_mod.EDIT_STYLES)}


@router.put("/projects/{pid}/brief")
def put_brief(pid: int, b: dict):
    from ..studio import brief as brief_mod
    _project(pid)
    return brief_mod.save(pid, b)


class AutoEditReq(BaseModel):
    brief: dict | None = None
    use_ai: bool = True
    retranscribe: bool = False
    llm_model: str | None = None
    models: dict[str, str] | None = None  # modelo por tarea (judge, director, writer)


@router.post("/projects/{pid}/autoedit")
def autoedit_project(pid: int, req: AutoEditReq):
    _project(pid)
    if any(j["kind"] == "studio_autoedit" for j in _active(pid)):
        raise HTTPException(409, "Ya se está auto-editando este proyecto.")
    from ..ai import models
    models.check(req.llm_model)
    if req.brief is not None:
        from ..studio import brief as brief_mod
        brief_mod.save(pid, req.brief)
    return jobs.enqueue("studio_autoedit", params={"project_id": pid, **req.model_dump(exclude={"brief"})})


def _active(pid: int) -> list[dict]:
    with db.get_conn() as conn:
        rows = conn.execute("SELECT * FROM jobs WHERE status IN ('queued','running') AND kind LIKE 'studio_%'"
                            ).fetchall()
    return [j for j in (jobs.row_to_dict(r) for r in rows) if (j["params"] or {}).get("project_id") == pid]


@router.get("/projects/{pid}/jobs")
def project_jobs(pid: int):
    return _active(pid)


class TimelineReq(BaseModel):
    timeline: dict
    note: str = "Edición manual"


@router.put("/projects/{pid}/timeline")
def put_timeline(pid: int, req: TimelineReq):
    p = _project(pid)
    tl = req.timeline
    try:
        for t in tlm.TRACKS:
            tl.setdefault("tracks", {}).setdefault(t, [])
        tl["width"], tl["height"], tl["fps"] = p["timeline"]["width"], p["timeline"]["height"], p["timeline"]["fps"]
        tl.setdefault("captions", {"enabled": True})
        valid_assets = {a["id"] for a in assets.list_assets()}
        with db.get_conn() as conn:
            valid_videos = {r["id"] for r in conn.execute("SELECT id FROM videos").fetchall()}
        tl["tracks"]["broll"] = [x for x in tl["tracks"]["broll"]
                                 if x.get("asset_id") in valid_assets or x.get("video_id") in valid_videos]
        for track in ("music", "sfx"):
            tl["tracks"][track] = [x for x in tl["tracks"][track] if x.get("asset_id") in valid_assets]
        for track in ("video", "broll"):
            for x in tl["tracks"][track]:
                if x.get("transition") not in render.TRANSITIONS:
                    x["transition"] = "cut"
        for x in tl["tracks"]["text"]:
            x["text"] = str(x.get("text", ""))[:140]
            if x.get("kind") not in tlm.TEXT_KINDS:
                x["kind"] = "callout"
        return projects.public(projects.save(pid, tl, note=req.note[:200]))
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(400, f"Timeline inválido: {exc}")


class StyleReq(BaseModel):
    style: dict
    as_default: bool = False


@router.put("/projects/{pid}/style")
def put_style(pid: int, req: StyleReq):
    _project(pid)
    clean = style_mod.clean_style(req.style)
    if req.as_default:
        db.set_setting("studio_default_style", clean)
    return projects.public(projects.save(pid, style=clean, note="Cambio de estilo"))


@router.post("/projects/{pid}/undo")
def undo(pid: int):
    _project(pid)
    return projects.public(projects.step(pid, -1))


@router.post("/projects/{pid}/redo")
def redo(pid: int):
    _project(pid)
    return projects.public(projects.step(pid, +1))


class InstructReq(BaseModel):
    text: str = Field(min_length=2, max_length=800)
    llm_model: str | None = None
    models: dict[str, str] | None = None  # modelo por tarea (judge, director, writer)


@router.post("/projects/{pid}/instruct")
def instruct(pid: int, req: InstructReq):
    _project(pid)
    if not agent.llm_ready():
        raise HTTPException(503, "Ollama no está disponible: ábrelo para usar las instrucciones de IA.")
    from ..ai import models
    return jobs.enqueue("studio_instruct", params={"project_id": pid, "text": req.text.strip(),
                                                   "llm_model": models.check(req.llm_model)})


class BackgroundSearchReq(BaseModel):
    query: str = Field(min_length=2, max_length=200)
    limit: int = Field(default=8, ge=1, le=20)


@router.post("/projects/{pid}/background/search")
def background_search(pid: int, req: BackgroundSearchReq):
    """Busca videos de gameplay/trailer en internet relacionados con la consulta,
    para que el usuario elija uno y lo ponga como fondo (add_background con video_id o URL)."""
    _project(pid)
    q = req.query.strip()
    if any(w in q.lower() for w in ("trailer", "gameplay", "tráiler")):
        search_q = q
    else:
        search_q = f"{q} gameplay"
    try:
        results = research.videos(search_q, n=req.limit)
    except research.ResearchError as exc:
        raise HTTPException(502, f"No se pudo buscar en internet: {exc}")
    return {"query": q, "results": results}


@router.post("/projects/{pid}/chroma/detect")
def chroma_detect(pid: int):
    """Mide el color real de tu pantalla verde (según tu iluminación) en el primer tramo del proyecto."""
    from ..studio import chroma
    p = _project(pid)
    seg = (p["timeline"]["tracks"]["video"] or [{}])[0]
    det = chroma.detect_color(p["source_path"], float(seg.get("src_in", 0)) + 1.0)
    if det["detected"]:  # recuadro de tu cámara que nunca debe volverse transparente
        ch = chroma.normalize({**(p["timeline"].get("chroma") or {}), "color": det["color"],
                               "similarity": det["similarity"]})
        det["protect"] = chroma.detect_protect(p["source_path"], ch, chroma.sample_times(p["timeline"]))
    return det


class ReferenceReq(BaseModel):
    video_id: int | None = None
    upload_id: str | None = None


@router.post("/projects/{pid}/reference")
def reference(pid: int, req: ReferenceReq):
    _project(pid)
    return jobs.enqueue("studio_reference", params={"project_id": pid, **req.model_dump()})


class SplitReq(BaseModel):
    time: float


@router.post("/projects/{pid}/split")
def split(pid: int, req: SplitReq):
    p = _project(pid)
    tl = p["timeline"]
    if not tlm.split_at(tl, req.time):
        raise HTTPException(400, "No se puede dividir en ese punto (muy cerca de un corte).")
    return projects.public(projects.save(pid, tl, note="Dividir segmento"))


class RangeReq(BaseModel):
    start: float
    end: float


@router.post("/projects/{pid}/remove-range")
def remove_range(pid: int, req: RangeReq):
    p = _project(pid)
    tl = p["timeline"]
    if tlm.remove_range(tl, req.start, req.end) <= 0:
        raise HTTPException(400, "Rango inválido.")
    return projects.public(projects.save(pid, tl, note="Cortar tramo"))


class RenderReq(BaseModel):
    quality: Literal["preview", "hd", "final"] = "preview"


@router.post("/projects/{pid}/render")
def render_project(pid: int, req: RenderReq):
    p = _project(pid)
    if not p["timeline"]["tracks"]["video"]:
        raise HTTPException(400, "El timeline está vacío.")
    return jobs.enqueue("studio_render", params={"project_id": pid, "quality": req.quality})


@router.get("/projects/{pid}/renders")
def project_renders(pid: int):
    _project(pid)
    with db.get_conn() as conn:
        rows = conn.execute("SELECT * FROM studio_renders WHERE project_id = ? ORDER BY id DESC", (pid,)).fetchall()
    return [projects.render_dict(r) for r in rows]


@router.get("/renders/{rid}/file")
def render_file(rid: int, request: Request, download: bool = False):
    with db.get_conn() as conn:
        r = conn.execute("SELECT * FROM studio_renders WHERE id = ?", (rid,)).fetchone()
    if not r:
        raise HTTPException(404, "Render no encontrado.")
    return serve_file(Path(r["path"]), request, "video/mp4", download)


@router.get("/finals")
def list_finals(limit: int = 200):
    """Videos finales exportados desde el Studio (separados de los clips de «Clips con IA»)."""
    with db.get_conn() as conn:
        rows = conn.execute("""SELECT r.id, r.project_id, r.path, r.duration, r.size_bytes, r.created_at, p.name,
                                      (SELECT v.id FROM videos v WHERE v.original_path = r.path) AS library_video_id
                               FROM studio_renders r JOIN studio_projects p ON p.id = r.project_id
                               WHERE r.quality = 'final' ORDER BY r.id DESC LIMIT ?""", (limit,)).fetchall()
    out = []
    for r in rows:
        ok = Path(r["path"]).exists()
        out.append({"id": r["id"], "project_id": r["project_id"], "title": r["name"], "duration": r["duration"],
                    "size_bytes": r["size_bytes"], "created_at": r["created_at"], "available": ok,
                    "file_name": Path(r["path"]).name, "url": f"/api/studio/renders/{r['id']}/file",
                    "library_video_id": r["library_video_id"]})
    return out


@router.post("/renders/{rid}/to-library")
def render_to_library(rid: int):
    """Lleva un video ya editado en el Studio a «Clips con IA» para sacarle clips (sin copiar el archivo)."""
    with db.get_conn() as conn:
        r = conn.execute("""SELECT r.path, p.name FROM studio_renders r JOIN studio_projects p ON p.id = r.project_id
                            WHERE r.id = ?""", (rid,)).fetchone()
        if not r:
            raise HTTPException(404, "Render no encontrado.")
        hit = conn.execute("SELECT id FROM videos WHERE original_path = ?", (r["path"],)).fetchone()
    if hit:
        return {"video_id": hit["id"], "created": False}
    src = Path(r["path"])
    if not src.exists():
        raise HTTPException(404, "El archivo exportado ya no existe en el disco.")
    try:
        meta = media.probe(src)
    except media.MediaError as exc:
        raise HTTPException(422, str(exc))
    with db.get_conn() as conn:
        vid = conn.execute(
            """INSERT INTO videos (title, original_path, stored_path, storage_mode,
                   size_bytes, duration, width, height, fps, video_codec, audio_codec, has_audio)
               VALUES (?, ?, ?, 'reference', ?, ?, ?, ?, ?, ?, ?, ?)""",
            (f"Studio: {r['name']}"[:200], str(src), str(src), meta["size_bytes"], meta["duration"], meta["width"],
             meta["height"], meta["fps"], meta["video_codec"], meta["audio_codec"], int(meta["has_audio"]))).lastrowid
    thumb = config.THUMBS_DIR / f"{vid}.jpg"
    if media.make_thumbnail(src, thumb, meta["duration"]):
        with db.get_conn() as conn:
            conn.execute("UPDATE videos SET thumbnail = ? WHERE id = ?", (thumb.name, vid))
    return {"video_id": vid, "created": True}


@router.delete("/renders/{rid}")
def delete_render(rid: int):
    with db.get_conn() as conn:
        r = conn.execute("SELECT * FROM studio_renders WHERE id = ?", (rid,)).fetchone()
        if not r:
            raise HTTPException(404, "Render no encontrado.")
        p = Path(r["path"])
        if security.is_within(p, config.STUDIO_RENDERS_DIR):
            try:
                p.unlink(missing_ok=True)
            except OSError:  # en uso (Windows): se omite
                pass
        conn.execute("DELETE FROM studio_renders WHERE id = ?", (rid,))
    return {"ok": True}


# ---------- recursos ----------

@router.get("/assets")
def list_assets(kind: Literal["music", "sfx", "broll"] | None = None):
    assets.ensure_starter_pack()
    return assets.list_assets(kind)


class ScanReq(BaseModel):
    folder: str
    kind: Literal["music", "sfx", "broll"] | None = None


@router.post("/assets/scan")
def scan(req: ScanReq):
    folder = security.require_authorized(req.folder.strip().strip('"'))
    if not folder.is_dir():
        raise HTTPException(400, "La ruta no es una carpeta.")
    res = assets.scan_folder(folder, req.kind)
    folders = db.get_setting("studio_asset_folders", []) or []
    if str(folder) not in folders:
        db.set_setting("studio_asset_folders", folders + [str(folder)])
    return res


class AssetPatch(BaseModel):
    kind: Literal["music", "sfx", "broll"] | None = None
    title: str | None = Field(None, min_length=1, max_length=120)
    tags: str | None = Field(None, max_length=300)


@router.patch("/assets/{aid}")
def patch_asset(aid: int, req: AssetPatch):
    a = assets.get_asset(aid)
    if not a:
        raise HTTPException(404, "Recurso no encontrado.")
    fields = req.model_dump(exclude_none=True)
    if fields.get("kind") in ("music", "sfx") and a["media_type"] != "audio":
        raise HTTPException(400, "Solo un audio puede ser música o efecto.")
    if fields.get("kind") == "broll" and a["media_type"] == "audio":
        raise HTTPException(400, "Un audio no puede ser b-roll.")
    if fields:
        with db.get_conn() as conn:
            conn.execute(f"UPDATE assets SET {', '.join(f'{k} = ?' for k in fields)} WHERE id = ?",
                         (*fields.values(), aid))
    return assets.get_asset(aid)


@router.delete("/assets/{aid}")
def delete_asset(aid: int):
    """Quita el recurso de la biblioteca (no borra tu archivo)."""
    with db.get_conn() as conn:
        conn.execute("DELETE FROM assets WHERE id = ? AND builtin = 0", (aid,))
    return {"ok": True}


@router.get("/assets/{aid}/file")
def asset_file(aid: int, request: Request):
    a = assets.get_asset(aid)
    if not a:
        raise HTTPException(404, "Recurso no encontrado.")
    p = Path(a["path"])
    mime = media.guess_mime(p) if a["media_type"] == "video" else (
        "image/jpeg" if a["media_type"] == "image" else "audio/mpeg")
    if a["media_type"] == "audio":
        import mimetypes
        mime = mimetypes.guess_type(str(p))[0] or "audio/wav"
    return serve_file(p, request, mime)


# ---------- tu voz ----------

class VoiceReq(BaseModel):
    upload_id: str


@router.get("/voice")
def get_voice():
    return {"profile": voice.profile(), "tts_ready": voice.tts_available(),
            "emotions": {k: v["label"] for k, v in voice.EMOTIONS.items()}}


@router.post("/voice")
def create_voice(req: VoiceReq):
    path = config.STUDIO_MEDIA_DIR / req.upload_id
    if "/" in req.upload_id or "\\" in req.upload_id or not path.exists():
        raise HTTPException(400, "Subida inválida.")
    return jobs.enqueue("studio_voice", params={"upload_id": req.upload_id})


class VoiceTestReq(BaseModel):
    text: str = Field(min_length=2, max_length=400)
    emotion: str = "neutral"


@router.post("/voice/test")
def test_voice(req: VoiceTestReq):
    if not voice.tts_available():
        raise HTTPException(503, "El motor de voz no está instalado.")
    return jobs.enqueue("studio_voice_test", params=req.model_dump())


@router.get("/voice/file/{name}")
def voice_file(name: str, request: Request):
    if "/" in name or "\\" in name or not name.endswith(".wav"):
        raise HTTPException(400, "Nombre inválido.")
    return serve_file(voice.VOICE_DIR / name, request, "audio/wav")


# ---------- índice de tus clips ----------

@router.get("/footage")
def footage_overview():
    return footage.overview()


@router.post("/footage/import-all")
def footage_import_all():
    """Importa a la biblioteca (sin copiar) todos los videos de tus carpetas autorizadas."""
    from ..routers.library import _import_one
    import os
    found = []
    for root in security.authorized_roots():
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if not d.startswith((".", "$"))]
            for fn in filenames:
                if Path(fn).suffix.lower() in config.VIDEO_EXTS:
                    found.append(str(Path(dirpath) / fn))
    res = [_import_one(p, "reference") for p in found[:300]]
    return {"found": len(found), "imported": sum(1 for r in res if r["ok"])}


class IndexReq(BaseModel):
    video_ids: list[int] | None = None
    force: bool = False


@router.post("/footage/index")
def footage_index(req: IndexReq):
    return jobs.enqueue("studio_footage", params=req.model_dump())


@router.get("/footage/{video_id}/shots")
def footage_shots(video_id: int):
    footage.ensure_schema()
    with db.get_conn() as conn:
        rows = conn.execute("SELECT * FROM footage_shots WHERE video_id = ? ORDER BY start_s", (video_id,)).fetchall()
    return [{**dict(r), "thumb_url": f"/api/studio/footage/thumb/{r['id']}"} for r in rows]


@router.get("/footage/thumb/{shot_id}")
def footage_thumb(shot_id: int, request: Request):
    footage.ensure_schema()
    with db.get_conn() as conn:
        r = conn.execute("SELECT thumb FROM footage_shots WHERE id = ?", (shot_id,)).fetchone()
    if not r or not r["thumb"]:
        raise HTTPException(404, "Sin miniatura.")
    return serve_file(Path(r["thumb"]), request, "image/jpeg")


# ---------- video desde un tema ----------

class TopicReq(BaseModel):
    topic: str = Field(min_length=3, max_length=500)
    notes: str | None = Field(None, max_length=800)
    duration: int = Field(45, ge=15, le=180)
    format: Literal["vertical", "horizontal", "square"] = "vertical"
    video_ids: list[int] | None = None
    use_voice: bool = True
    name: str | None = Field(None, max_length=120)
    style_id: int | None = None
    models: dict[str, str] | None = None


@router.post("/topic")
def create_from_topic(req: TopicReq):
    if not agent.llm_ready():
        raise HTTPException(503, "Ollama no está disponible: ábrelo para escribir el guion.")
    if not voice.tts_available():
        raise HTTPException(503, "El motor de voz no está instalado todavía.")
    prm = req.model_dump()
    if req.style_id:
        prm["style"] = learn.style_for(req.style_id)
    return jobs.enqueue("studio_topic", params=prm)


# ---------- estilos aprendidos ----------

class LearnSource(BaseModel):
    url: str | None = Field(None, max_length=2000)
    video_id: int | None = None
    upload_id: str | None = None
    source_name: str | None = None


class LearnReq(BaseModel):
    video_id: int | None = None
    upload_id: str | None = None
    name: str | None = Field(None, max_length=120)
    source_name: str | None = None
    urls: str | list[str] | None = None          # enlaces (YouTube, TikTok, Instagram…), uno por línea
    video_ids: list[int] | None = None            # varios videos de tu biblioteca
    sources: list[LearnSource] | None = None      # mezcla libre de orígenes
    style_id: int | None = None                   # sumar los videos a un estilo existente


def _has_ytdlp() -> bool:
    import importlib.util
    return importlib.util.find_spec("yt_dlp") is not None


@router.post("/learn")
def learn_style(req: LearnReq):
    from ..studio import refs
    n = len(refs.parse_urls(req.urls)) + len(req.video_ids or []) + len(req.sources or []) + \
        int(bool(req.video_id)) + int(bool(req.upload_id))
    if n == 0:
        raise HTTPException(400, "Agrega al menos un enlace o un video.")
    if n > 12:
        raise HTTPException(400, "Máximo 12 videos por vez (puedes sumar más después al mismo estilo).")
    if req.style_id is not None:
        learn.ensure_schema()
        with db.get_conn() as conn:
            if not conn.execute("SELECT 1 FROM learned_styles WHERE id = ?", (req.style_id,)).fetchone():
                raise HTTPException(404, "El estilo a ampliar no existe.")
    return jobs.enqueue("studio_learn", params=req.model_dump())


@router.get("/styles")
def styles():
    return learn.list_styles()


@router.get("/ref-cookies")
def ref_cookies():
    from ..studio import refs
    return {"browser": db.get_setting("ytdlp_cookies_browser") or "", "file": refs.COOKIE_FILE.exists(),
            "browsers": list(refs.BROWSERS)}


class CookieBrowserReq(BaseModel):
    browser: str = ""


@router.put("/ref-cookies")
def set_ref_cookies(req: CookieBrowserReq):
    from ..studio import refs
    b = req.browser.strip().lower()
    if b and b not in refs.BROWSERS:
        raise HTTPException(400, "Navegador no soportado.")
    db.set_setting("ytdlp_cookies_browser", b)
    return ref_cookies()


@router.post("/ref-cookies/file")
async def upload_ref_cookies(file: UploadFile = File(...)):
    from ..studio import refs
    data = await file.read()
    text = data[:200000].decode("utf-8", "ignore")
    if "# Netscape HTTP Cookie File" not in text and "\t" not in text:
        raise HTTPException(400, "No parece un archivo cookies.txt (formato Netscape).")
    refs.COOKIE_FILE.parent.mkdir(parents=True, exist_ok=True)
    refs.COOKIE_FILE.write_bytes(data)
    return ref_cookies()


@router.delete("/ref-cookies/file")
def delete_ref_cookies():
    from ..studio import refs
    refs.COOKIE_FILE.unlink(missing_ok=True)
    return ref_cookies()


@router.delete("/styles/{sid}")
def delete_style(sid: int):
    learn.ensure_schema()
    with db.get_conn() as conn:
        row = conn.execute("SELECT style FROM learned_styles WHERE id = ?", (sid,)).fetchone()
        if not row:
            raise HTTPException(404, "Estilo no encontrado.")
        conn.execute("DELETE FROM learned_styles WHERE id = ?", (sid,))
    # si era el estilo por defecto, vuelve al estilo base
    if row["style"] and db.get_setting("studio_default_style") == json.loads(row["style"]):
        db.set_setting("studio_default_style", None)
    return {"ok": True}


class ApplyStyleReq(BaseModel):
    style_id: int
    as_default: bool = False


@router.post("/projects/{pid}/apply-style")
def apply_style(pid: int, req: ApplyStyleReq):
    _project(pid)
    st = learn.style_for(req.style_id)
    if req.as_default:
        db.set_setting("studio_default_style", st)
    return projects.public(projects.save(pid, style=st, note="Estilo aprendido aplicado"))


@router.post("/styles/{sid}/default")
def style_default(sid: int):
    db.set_setting("studio_default_style", learn.style_for(sid))
    return {"ok": True}


# ---------- trabajos ----------

def run_instruct(job: dict, progress) -> dict:
    pid = job["params"]["project_id"]
    p = projects.get(pid)
    progress(0.1, "La IA está interpretando tu instrucción")
    tl, st, message, notes = agent.instruct(p["timeline"], p["style"], job["params"]["text"],
                                            source_path=p.get("source_path"))
    progress(0.9, "Aplicando cambios")
    projects.save(pid, tl, st, note=f"IA: {job['params']['text'][:120]}")
    return {"message": message, "skipped": notes}


def run_reference(job: dict, progress) -> dict:
    prm = job["params"]
    pid = prm["project_id"]
    if prm.get("video_id"):
        with db.get_conn() as conn:
            v = conn.execute("SELECT * FROM videos WHERE id = ?", (prm["video_id"],)).fetchone()
        if not v:
            raise RuntimeError("Video de referencia no encontrado.")
        path, dur = Path(v["stored_path"]), v["duration"]
    else:
        path = config.STUDIO_MEDIA_DIR / str(prm.get("upload_id"))
        dur = probe_any(path)["duration"]
    progress(0.1, "Analizando el ritmo del video de referencia")
    with db.get_conn() as conn:
        tr = conn.execute("SELECT segments FROM transcripts WHERE video_id = ?", (prm.get("video_id") or 0,)).fetchone()
    words = [w for s in json.loads(tr["segments"]) for w in s.get("words", [])] if tr else None
    res = style_mod.analyze_reference(path, dur or 0, words)
    p = projects.get(pid)
    projects.save(pid, style={**p["style"], **res["changes"]}, note="Ritmo de video de referencia")
    return res


jobs.HANDLERS["studio_autoedit"] = autoedit.run_autoedit
jobs.HANDLERS["studio_render"] = render.run_studio_render
jobs.HANDLERS["studio_instruct"] = run_instruct
jobs.HANDLERS["studio_reference"] = run_reference


def run_voice(job: dict, progress) -> dict:
    prof = voice.create_profile(config.STUDIO_MEDIA_DIR / job["params"]["upload_id"], progress)
    return {"duration": prof["duration"], "emotions": sorted(prof["refs"])}


def run_voice_test(job: dict, progress) -> dict:
    import time as _t
    work = config.WORK_DIR / f"voicetest_{job['id']}"
    out, _ = voice.synthesize([{"text": job["params"]["text"], "emotion": job["params"]["emotion"]}], work, progress)
    voice.VOICE_DIR.mkdir(parents=True, exist_ok=True)
    name = f"prueba_{int(_t.time())}.wav"
    import shutil as _sh
    _sh.copy2(out, voice.VOICE_DIR / name)
    _sh.rmtree(work, ignore_errors=True)
    return {"url": f"/api/studio/voice/file/{name}"}


jobs.HANDLERS["studio_voice"] = run_voice
jobs.HANDLERS["studio_voice_test"] = run_voice_test
jobs.HANDLERS["studio_footage"] = footage.run_index
jobs.HANDLERS["studio_topic"] = topic.run_topic
jobs.HANDLERS["studio_learn"] = learn.run_learn
