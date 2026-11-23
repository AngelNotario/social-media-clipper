"""API del motor de IA, clips candidatos, render y exportaciones."""
from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from .. import config, db, jobs, media, security
from ..ai import gpu, highlights, llm
from ..ai.pipeline import load_transcript
from ..editor import render
from .library import _parse_range

router = APIRouter(tags=["ai"])


# ---------- estado ----------

@router.get("/ai/models")
def ai_models():
    from ..ai import models
    return models.installed()


def _judge_model(url: str, model: str) -> str | None:
    from ..ai import judge
    return judge.pick_model(url, model, db.get_setting("judge_model"))


@router.get("/ai/status")
def ai_status():
    try:
        import faster_whisper  # noqa: F401
        whisper_ok = True
    except Exception:
        whisper_ok = False
    url, model = db.get_setting("ollama_url"), db.get_setting("ollama_model")
    ol = llm.status(url)
    return {
        "vocabulary": db.get_setting("vocabulary") or [],
        "vocab_fixes": db.get_setting("vocab_fixes") or {},
        "whisper": {"installed": whisper_ok, "model": db.get_setting("whisper_model"),
                    "language": db.get_setting("whisper_language"),
                    "cuda": gpu.cuda_available() if whisper_ok else False},
        "ollama": {"url": url, "model": model, "available": ol["available"],
                   "models": ol["models"], "model_installed": model in ol["models"]},
        "judge": {"setting": db.get_setting("judge_model") or "",
                  "model": _judge_model(url, model) if ol["available"] else None},
        "roles": {"analysis": model, "judge": db.get_setting("judge_model") or "",
                  "director": db.get_setting("director_model") or "", "writer": db.get_setting("writer_model") or "",
                  "thumbnail": db.get_setting("thumbnail_model") or ""},
        "backend": ol.get("backend", "ollama"),
        "llamacpp": {"url": llm.llamacpp_url(), "alias": llm.llamacpp_alias()} if ol.get("backend") == "llamacpp" else None,
        "nvenc": gpu.nvenc_available() if media.ffmpeg_available() else False,
        "criteria": highlights.CRITERIA_LABELS,
    }


class AISettings(BaseModel):
    whisper_model: str | None = None
    whisper_language: str | None = None
    ollama_url: str | None = None
    ollama_model: str | None = None
    judge_model: str | None = None
    director_model: str | None = None
    writer_model: str | None = None
    thumbnail_model: str | None = None
    research_enabled: bool | None = None
    llm_backend: Literal["ollama", "llamacpp"] | None = None
    vocabulary: list[str] | str | None = None


@router.put("/ai/settings")
def ai_settings(req: AISettings):
    for k, val in req.model_dump(exclude_none=True).items():
        if k == "vocabulary":  # «Eneba, GTA 6» o una lista → lista limpia sin repetidos
            import re
            items = re.split(r"[,\n;]+", val) if isinstance(val, str) else val
            seen, out = set(), []
            for t in items:
                t = str(t).strip()[:60]
                if t and t.lower() not in seen:
                    seen.add(t.lower())
                    out.append(t)
            db.set_setting(k, out[:200])
            continue
        db.set_setting(k, val.strip() if isinstance(val, str) else val)
    return ai_status()


# ---------- motor llama.cpp ----------

class LlamaCfg(BaseModel):
    llamacpp_exe: str | None = None
    llamacpp_model: str | None = None
    llamacpp_draft: str | None = None
    llamacpp_mmproj: str | None = None
    llamacpp_ctx: int | None = Field(None, ge=4096, le=262144)
    llamacpp_url: str | None = None
    llamacpp_alias: str | None = None
    llamacpp_autostart: bool | None = None
    llamacpp_extra: str | None = None


@router.get("/ai/llamacpp")
def llamacpp_status():
    from ..ai import llamacpp_server
    return llamacpp_server.status()


@router.put("/ai/llamacpp")
def llamacpp_config(req: LlamaCfg):
    from ..ai import llamacpp_server
    vals = req.model_dump(exclude_none=True)
    if "llamacpp_autostart" in vals:
        vals["llamacpp_autostart"] = "1" if vals["llamacpp_autostart"] else "0"
    llamacpp_server.save(vals)
    return llamacpp_server.status()


@router.post("/ai/llamacpp/start")
def llamacpp_start():
    from ..ai import llamacpp_server
    return llamacpp_server.start()


@router.post("/ai/llamacpp/stop")
def llamacpp_stop():
    from ..ai import llamacpp_server
    return llamacpp_server.stop()


@router.post("/ai/llamacpp/test")
def llamacpp_test():
    """Prueba de velocidad: una respuesta corta (y una con imagen si hay visión)."""
    import base64
    import io
    import time as _t
    from ..ai import llamacpp_server
    if llamacpp_server.health() != "ok":
        raise HTTPException(409, "El servidor de llama.cpp no está listo todavía.")
    base = llm.llamacpp_url()
    t0 = _t.time()
    try:
        import httpx
        r = httpx.post(f"{base}/v1/chat/completions", timeout=300, json={
            "model": llm.llamacpp_alias(), "max_tokens": 256, "temperature": 0.3,
            "chat_template_kwargs": {"enable_thinking": False},
            "messages": [{"role": "user", "content": "Escribe 3 títulos virales para un video sobre retrasos de entrega de códigos de GTA 6 en una tienda online."}]})
        r.raise_for_status()
        j = r.json()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"llama.cpp falló: {exc}")
    tm = j.get("timings") or {}
    out = {"text": (j.get("choices") or [{}])[0].get("message", {}).get("content", "")[:600],
           "tokens_per_s": round(float(tm.get("predicted_per_second") or 0), 1),
           "prompt_per_s": round(float(tm.get("prompt_per_second") or 0), 1),
           "draft_accepted": tm.get("draft_n_accepted"), "draft_total": tm.get("draft_n"),
           "seconds": round(_t.time() - t0, 1), "vision": None}
    if llamacpp_server.files_ok()["llamacpp_mmproj"]:
        try:
            from PIL import Image, ImageDraw
            im = Image.new("RGB", (320, 180), "#1e3a8a")
            ImageDraw.Draw(im).text((20, 70), "GTA 6", fill="white")
            buf = io.BytesIO()
            im.save(buf, "JPEG")
            t1 = _t.time()
            v = llm.chat_json("", "", "Responde en JSON.", "¿Qué color de fondo y qué texto hay en la imagen?",
                              {"type": "object", "properties": {"fondo": {"type": "string"}, "texto": {"type": "string"}},
                               "required": ["fondo", "texto"]}, timeout=300,
                              images=[base64.b64encode(buf.getvalue()).decode()], max_tokens=80)
            out["vision"] = {"ok": True, "answer": v, "seconds": round(_t.time() - t1, 1)}
        except Exception as exc:  # noqa: BLE001
            out["vision"] = {"ok": False, "error": str(exc)[:200]}
    return out


# ---------- análisis ----------

class AnalyzeRequest(BaseModel):
    max_clips: int = Field(10, ge=1, le=30)
    min_dur: float = Field(20, ge=5, le=180)
    max_dur: float = Field(60, ge=10, le=300)
    retranscribe: bool = False
    mode: Literal["speech", "stream"] = "speech"
    llm_model: str | None = None
    judge: bool = True
    judge_model: str | None = None
    models: dict[str, str] | None = None  # {"judge": ..., "director": ..., "writer": ...}


def _video(video_id: int):
    with db.get_conn() as conn:
        v = conn.execute("SELECT * FROM videos WHERE id = ?", (video_id,)).fetchone()
    if not v:
        raise HTTPException(404, "Video no encontrado.")
    return v


@router.post("/videos/{video_id}/analyze")
def analyze(video_id: int, req: AnalyzeRequest):
    _video(video_id)
    if req.max_dur <= req.min_dur:
        raise HTTPException(400, "La duración máxima debe ser mayor que la mínima.")
    if jobs.active_for(video_id=video_id, kind="analyze"):
        raise HTTPException(409, "Este video ya se está analizando.")
    from ..ai import models
    models.check(req.llm_model)
    models.check(req.judge_model)
    return jobs.enqueue("analyze", video_id=video_id, params=req.model_dump())


@router.get("/videos/{video_id}/transcript")
def transcript(video_id: int):
    tr = load_transcript(video_id)
    if not tr:
        raise HTTPException(404, "Este video aún no tiene transcripción.")
    return tr


@router.get("/videos/{video_id}/analysis")
def analysis(video_id: int):
    _video(video_id)
    with db.get_conn() as conn:
        a = conn.execute("SELECT method, llm_model, params, created_at, scenes FROM analyses WHERE video_id = ?",
                         (video_id,)).fetchone()
    job = jobs.active_for(video_id=video_id, kind="analyze")
    with db.get_conn() as conn:
        last = conn.execute("SELECT * FROM jobs WHERE video_id = ? AND kind = 'analyze' ORDER BY id DESC LIMIT 1",
                            (video_id,)).fetchone()
    return {
        "analysis": None if not a else {
            "method": a["method"], "llm_model": a["llm_model"], "created_at": a["created_at"],
            "params": json.loads(a["params"] or "{}"), "scenes": len(json.loads(a["scenes"] or "[]")),
        },
        "has_transcript": load_transcript(video_id) is not None,
        "job": job or (jobs.row_to_dict(last) if last else None),
    }


@router.get("/videos/overview")
def videos_overview():
    """Lista de videos con su estado de análisis y conteo de clips (para la página de Clips)."""
    with db.get_conn() as conn:
        rows = conn.execute("""
            SELECT v.id, v.title, v.duration, v.width, v.height, v.thumbnail, v.has_audio, v.stored_path,
                   a.method, a.created_at AS analyzed_at,
                   (SELECT COUNT(*) FROM clips c WHERE c.video_id = v.id) AS clips,
                   (SELECT COUNT(*) FROM clips c WHERE c.video_id = v.id AND c.status = 'approved') AS approved,
                   (SELECT status FROM jobs j WHERE j.video_id = v.id AND j.kind = 'analyze'
                      ORDER BY j.id DESC LIMIT 1) AS job_status,
                   EXISTS(SELECT 1 FROM studio_renders r WHERE r.path = v.original_path) AS from_studio
            FROM videos v LEFT JOIN analyses a ON a.video_id = v.id
            ORDER BY v.id DESC
        """).fetchall()
    from .. import library_sync
    library_sync.kick()
    return [{**{k: v for k, v in dict(r).items() if k != "stored_path"},
             "thumbnail_url": f"/api/library/{r['id']}/thumbnail" if r["thumbnail"] else None}
            for r in rows if library_sync.available(r["stored_path"])]  # sin videos que ya no existen


# ---------- trabajos ----------

@router.get("/jobs")
def list_jobs(video_id: int | None = None, active: bool = False, limit: int = 20):
    sql, params = "SELECT * FROM jobs WHERE 1=1", []
    if video_id is not None:
        sql += " AND video_id = ?"
        params.append(video_id)
    if active:
        sql += " AND status IN ('queued','running')"
    with db.get_conn() as conn:
        rows = conn.execute(sql + " ORDER BY id DESC LIMIT ?", (*params, limit)).fetchall()
    return [jobs.row_to_dict(r) for r in rows]


@router.get("/jobs/{job_id}")
def get_job(job_id: int):
    j = jobs.get(job_id)
    if not j:
        raise HTTPException(404, "Trabajo no encontrado.")
    return j


@router.post("/jobs/{job_id}/cancel")
def cancel_job(job_id: int):
    jobs.cancel(job_id)
    return jobs.get(job_id)


# ---------- clips ----------

def _clip_dict(row, segments: list[dict] | None = None) -> dict:
    d = dict(row)
    d["scores"] = json.loads(d["scores"]) if d.get("scores") else {}
    d["judge"] = json.loads(d["judge"]) if d.get("judge") else None
    d["duration"] = round(d["end_s"] - d["start_s"], 2)
    if segments is not None:
        d["text"] = highlights.text_between(segments, d["start_s"], d["end_s"])
    with db.get_conn() as conn:
        rs = conn.execute("SELECT * FROM renders WHERE clip_id = ? ORDER BY id DESC", (d["id"],)).fetchall()
    d["renders"] = [_render_dict(r) for r in rs]
    return d


def _render_dict(r) -> dict:
    d = dict(r)
    d["options"] = json.loads(d["options"] or "{}")
    d["available"] = Path(d["path"]).exists()
    d["file_name"] = Path(d["path"]).name
    d["url"] = f"/api/renders/{d['id']}/file"
    return d


def _get_clip(clip_id: int):
    with db.get_conn() as conn:
        row = conn.execute("SELECT * FROM clips WHERE id = ?", (clip_id,)).fetchone()
    if not row:
        raise HTTPException(404, "Clip no encontrado.")
    return row


@router.get("/videos/{video_id}/clips")
def video_clips(video_id: int, sort: Literal["score", "time"] = "score"):
    _video(video_id)
    tr = load_transcript(video_id)
    order = "score DESC NULLS LAST, id" if sort == "score" else "start_s"
    with db.get_conn() as conn:
        rows = conn.execute(f"SELECT * FROM clips WHERE video_id = ? ORDER BY {order}", (video_id,)).fetchall()
    segs = tr["segments"] if tr else []
    return [_clip_dict(r, segs) for r in rows]


@router.get("/clips/{clip_id}")
def get_clip(clip_id: int):
    row = _get_clip(clip_id)
    tr = load_transcript(row["video_id"])
    return _clip_dict(row, tr["segments"] if tr else [])


class JudgeRequest(BaseModel):
    llm_model: str | None = None


@router.post("/clips/{clip_id}/judge")
def judge_clip(clip_id: int, req: JudgeRequest):
    """Segunda opinión sobre un clip (con los tiempos actuales). Sugiere recortes sin aplicarlos."""
    from ..ai import judge, models
    models.check(req.llm_model)
    row = _get_clip(clip_id)
    tr = load_transcript(row["video_id"])
    segments = tr["segments"] if tr else []
    with db.get_conn() as conn:
        a = conn.execute("SELECT energy, params FROM analyses WHERE video_id = ?", (row["video_id"],)).fetchone()
        v = conn.execute("SELECT duration FROM videos WHERE id = ?", (row["video_id"],)).fetchone()
    energy = json.loads(a["energy"] or "[]") if a else []
    cfg = json.loads(a["params"] or "{}") if a else {}
    cfg.setdefault("min_dur", 15)
    url = db.get_setting("ollama_url")
    model = req.llm_model or judge.pick_model(url, db.get_setting("ollama_model"), db.get_setting("judge_model"))
    from .. import knowledge
    c = {"start": row["start_s"], "end": row["end_s"], "title": row["title"],
         "score": (json.loads(row["scores"] or "{}").get("seleccion")) or row["score"] or 50,
         "scores": json.loads(row["scores"] or "{}")}
    res = judge.judge_clip(c, segments, energy, cfg,
                           {"url": url, "model": model, "context": knowledge.prompt_context("clip", None, max_chars=1500)}
                           if model else None, v["duration"] if v else 0, apply=False)
    if res["judge"].get("error"):
        raise HTTPException(502, f"El juez no pudo evaluar el clip: {res['judge']['error']}")
    with db.get_conn() as conn:
        conn.execute("UPDATE clips SET judge = ?, scores = ?, score = ?, updated_at = datetime('now','localtime') "
                     "WHERE id = ?", (json.dumps(res["judge"], ensure_ascii=False), json.dumps(res["scores"]),
                                      res["score"], clip_id))
    return get_clip(clip_id)


class ClipCreate(BaseModel):
    video_id: int
    start_s: float = Field(ge=0)
    end_s: float = Field(gt=0)
    title: str = Field("Clip manual", min_length=1, max_length=200)


@router.post("/clips")
def create_clip(req: ClipCreate):
    v = _video(req.video_id)
    if req.end_s <= req.start_s or (v["duration"] and req.end_s > v["duration"] + 0.5):
        raise HTTPException(400, "Rango de tiempo inválido.")
    with db.get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO clips (video_id, start_s, end_s, title, source) VALUES (?, ?, ?, ?, 'manual')",
            (req.video_id, req.start_s, req.end_s, req.title.strip()),
        )
    return get_clip(cur.lastrowid)


class ClipUpdate(BaseModel):
    start_s: float | None = Field(None, ge=0)
    end_s: float | None = Field(None, gt=0)
    title: str | None = Field(None, min_length=1, max_length=200)
    hook: str | None = Field(None, max_length=300)
    status: Literal["pending", "approved", "discarded"] | None = None
    notes: str | None = Field(None, max_length=2000)


@router.patch("/clips/{clip_id}")
def update_clip(clip_id: int, req: ClipUpdate):
    row = _get_clip(clip_id)
    fields = req.model_dump(exclude_none=True)
    start = fields.get("start_s", row["start_s"])
    end = fields.get("end_s", row["end_s"])
    v = _video(row["video_id"])
    if end <= start + 0.5 or (v["duration"] and end > v["duration"] + 0.5):
        raise HTTPException(400, "Rango de tiempo inválido.")
    if fields:
        sets = ", ".join(f"{k} = ?" for k in fields) + ", updated_at = datetime('now','localtime')"
        with db.get_conn() as conn:
            conn.execute(f"UPDATE clips SET {sets} WHERE id = ?", (*fields.values(), clip_id))
    if "status" in fields and fields["status"] != row["status"]:
        from .. import taste
        try:  # aprobar/descartar enseña a la IA tu gusto (la foto sobrevive a un re-análisis)
            if fields["status"] == "pending":
                taste.forget(clip_id)
            else:
                taste.record(clip_id, 1 if fields["status"] == "approved" else -1, "status")
        except Exception:  # noqa: BLE001
            pass
    return get_clip(clip_id)


@router.delete("/clips/{clip_id}")
def delete_clip(clip_id: int):
    _get_clip(clip_id)
    with db.get_conn() as conn:
        rs = conn.execute("SELECT path FROM renders WHERE clip_id = ?", (clip_id,)).fetchall()
        conn.execute("DELETE FROM clips WHERE id = ?", (clip_id,))
    for r in rs:
        remove_export(Path(r["path"]))
    return {"ok": True}


# ---------- render / exportaciones ----------

class RenderRequest(BaseModel):
    crop_mode: Literal["face", "center", "blur", "fit", "stream"] = "face"
    subtitles: bool = True
    sub_style: Literal["karaoke", "simple"] = "karaoke"
    sub_position: Literal["auto", "bottom", "middle", "top"] = "auto"
    show_title: bool = False
    title: str | None = Field(None, max_length=120)
    style_id: int | None = None
    viral: bool = True
    pro_audio: bool = True
    clean_voice: bool = False
    qa: bool = True
    voice_mode: Literal["original", "realzar", "solo_voz"] = "original"
    music: bool = False
    beat_sync: bool = True
    models: dict[str, str] | None = None
    llm_model: str | None = None


@router.post("/clips/{clip_id}/render")
def render_clip(clip_id: int, req: RenderRequest):
    row = _get_clip(clip_id)
    if not media.ffmpeg_available():
        raise HTTPException(503, "FFmpeg no está disponible.")
    if jobs.active_for(clip_id=clip_id, kind="render"):
        raise HTTPException(409, "Este clip ya se está renderizando.")
    return jobs.enqueue("render", video_id=row["video_id"], clip_id=clip_id, params=req.model_dump())


@router.post("/clips/{clip_id}/edit")
def edit_clip(clip_id: int, req: RenderRequest):
    """Crea un proyecto del Studio con la edición de la IA (encuadre, subtítulos, efectos, música) para verlo
    en vivo y cambiar lo que quieras antes de exportar."""
    row = _get_clip(clip_id)
    from ..ai import models
    models.check(req.llm_model)
    for m in (req.models or {}).values():
        models.check(m)
    return jobs.enqueue("clip_edit", video_id=row["video_id"], clip_id=clip_id, params=req.model_dump())


def run_clip_edit(job: dict, progress) -> dict:
    from ..studio import clip_project
    try:
        return clip_project.create_project(job["clip_id"], job["params"] or {}, progress)
    except ValueError as exc:
        raise RuntimeError(str(exc)) from exc


@router.get("/renders")
def list_renders():
    with db.get_conn() as conn:
        rows = conn.execute("""
            SELECT r.*, c.title AS clip_title, c.video_id, c.start_s, c.end_s, v.title AS video_title
            FROM renders r JOIN clips c ON c.id = r.clip_id JOIN videos v ON v.id = c.video_id
            ORDER BY r.id DESC
        """).fetchall()
    return [{**_render_dict(r), "clip_title": r["clip_title"], "video_title": r["video_title"],
             "video_id": r["video_id"]} for r in rows]


def _get_render(render_id: int):
    with db.get_conn() as conn:
        r = conn.execute("SELECT * FROM renders WHERE id = ?", (render_id,)).fetchone()
    if not r:
        raise HTTPException(404, "Render no encontrado.")
    return r


@router.get("/renders/{render_id}/file")
def render_file(render_id: int, request: Request, download: bool = False):
    r = _get_render(render_id)
    p = Path(r["path"])
    if not p.exists():
        raise HTTPException(410, "El archivo ya no existe.")
    if download:
        return FileResponse(p, media_type="video/mp4", filename=p.name)
    from .library import CHUNK
    from fastapi.responses import StreamingResponse
    size = p.stat().st_size
    rh = request.headers.get("range")
    if not rh:
        return FileResponse(p, media_type="video/mp4", headers={"Accept-Ranges": "bytes"})
    rng = _parse_range(rh, size)
    if rng is None:
        raise HTTPException(416, "Rango inválido.", headers={"Content-Range": f"bytes */{size}"})
    start, end = rng

    def it():
        with open(p, "rb") as f:
            f.seek(start)
            left = end - start + 1
            while left > 0:
                chunk = f.read(min(CHUNK, left))
                if not chunk:
                    break
                left -= len(chunk)
                yield chunk

    return StreamingResponse(it(), status_code=206, media_type="video/mp4", headers={
        "Content-Range": f"bytes {start}-{end}/{size}", "Accept-Ranges": "bytes",
        "Content-Length": str(end - start + 1)})


def remove_export(p: Path) -> None:
    """Borra un clip generado. Si Windows lo tiene en uso (se está reproduciendo), queda pendiente y se borra después."""
    pending = [x for x in (db.get_setting("pending_delete") or []) if x != str(p)] + [str(p)]
    left = []
    for x in pending:
        q = Path(x)
        if not security.is_within(q, config.EXPORTS_DIR):
            continue
        try:
            q.unlink(missing_ok=True)
        except OSError:
            left.append(x)
    db.set_setting("pending_delete", left[-200:])


@router.delete("/renders/{render_id}")
def delete_render(render_id: int):
    r = _get_render(render_id)
    with db.get_conn() as conn:
        conn.execute("DELETE FROM renders WHERE id = ?", (render_id,))
    remove_export(Path(r["path"]))
    return {"ok": True}


def cleanup_video_exports(video_id: int) -> None:
    d = config.EXPORTS_DIR / f"video_{video_id}"
    if d.exists() and security.is_within(d, config.EXPORTS_DIR):
        shutil.rmtree(d, ignore_errors=True)


from ..ai.pipeline import run_analyze  # noqa: E402

jobs.HANDLERS["render"] = render.run_render
jobs.HANDLERS["clip_edit"] = run_clip_edit
jobs.HANDLERS["analyze"] = run_analyze
