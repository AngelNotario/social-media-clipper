"""API del modo stream: cámara, pista de voz, clips y resumen para YouTube."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field

from .. import config, db, jobs, stream
from ..ai.audio import audio_tracks

router = APIRouter(prefix="/stream", tags=["stream"])


def _video(video_id: int) -> dict:
    with db.get_conn() as conn:
        v = conn.execute("SELECT * FROM videos WHERE id = ?", (video_id,)).fetchone()
    if not v:
        raise HTTPException(404, "Video no encontrado.")
    return dict(v)


class Rect(BaseModel):
    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)
    w: float = Field(gt=0.02, le=1)
    h: float = Field(gt=0.02, le=1)


class SettingsIn(BaseModel):
    facecam: Rect | None = None
    clear_facecam: bool = False
    cam_mode: Literal["auto", "always", "never"] | None = None
    voice_track: int | None = Field(None, ge=-1, le=8)  # -1 = usar la mezcla


@router.get("/videos/{video_id}")
def get_settings(video_id: int):
    v = _video(video_id)
    src = Path(v["stored_path"])
    st = stream.settings(video_id)
    n = audio_tracks(src) if src.exists() else 0
    with db.get_conn() as conn:
        clips = conn.execute("SELECT COUNT(*) FROM clips WHERE video_id = ?", (video_id,)).fetchone()[0]
        projs = conn.execute("SELECT id, name, source_meta, updated_at FROM studio_projects WHERE source_path = ? "
                             "ORDER BY id DESC", (str(src),)).fetchall()
    youtube = []
    for p in projs:
        meta = json.loads(p["source_meta"] or "{}").get("youtube")
        if meta:
            youtube.append({"project_id": p["id"], "name": p["name"], "updated_at": p["updated_at"], **meta})
    return {**st, "audio_tracks": n, "auto_voice_track": stream.voice_track(video_id, src) if n else None,
            "clips": clips, "youtube": youtube, "video": {k: v[k] for k in ("id", "title", "duration", "width", "height")}}


@router.put("/videos/{video_id}")
def put_settings(video_id: int, body: SettingsIn):
    _video(video_id)
    fields = {}
    if body.clear_facecam:
        fields["facecam"] = None
    elif body.facecam:
        r = body.facecam
        fields["facecam"] = {"x": r.x, "y": r.y, "w": min(r.w, 1 - r.x), "h": min(r.h, 1 - r.y)}
    if body.cam_mode:
        fields["cam_mode"] = body.cam_mode
    if "voice_track" in body.model_fields_set:
        fields["voice_track"] = body.voice_track
    return stream.save_settings(video_id, **fields)


@router.post("/videos/{video_id}/detect-cam")
def detect_cam(video_id: int):
    v = _video(video_id)
    rect = stream.detect_facecam(Path(v["stored_path"]), v["duration"] or 0)
    if rect:
        stream.save_settings(video_id, facecam=rect)
    return {"facecam": rect, "found": rect is not None}


@router.get("/videos/{video_id}/frame")
def frame(video_id: int, t: float | None = None):
    v = _video(video_id)
    at = t if t is not None else (v["duration"] or 10) * 0.37
    r = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{max(0.0, at):.2f}", "-i", v["stored_path"],
                        "-frames:v", "1", "-vf", "scale=960:-2", "-q:v", "4", "-f", "image2pipe", "-vcodec", "mjpeg", "-"],
                       capture_output=True, timeout=60)
    if not r.stdout:
        raise HTTPException(500, "No se pudo leer un cuadro del video.")
    return Response(r.stdout, media_type="image/jpeg", headers={"Cache-Control": "no-store"})


class ClipsIn(BaseModel):
    max_clips: int = Field(12, ge=1, le=30)
    min_dur: float = Field(20, ge=5, le=180)
    max_dur: float = Field(60, ge=10, le=300)
    llm_model: str | None = None
    models: dict[str, str] | None = None  # modelo por tarea (judge, director, writer)


@router.post("/videos/{video_id}/clips")
def find_clips(video_id: int, body: ClipsIn):
    _video(video_id)
    if body.max_dur <= body.min_dur:
        raise HTTPException(400, "La duración máxima debe ser mayor que la mínima.")
    from ..ai import models
    models.check(body.llm_model)
    return jobs.enqueue("analyze", video_id=video_id, params={**body.model_dump(), "retranscribe": False,
                                                              "mode": "stream"})


class YoutubeIn(BaseModel):
    target_min: float = Field(17, ge=3, le=40)
    cold_open: bool = True
    captions: bool = True
    llm_model: str | None = None
    models: dict[str, str] | None = None  # modelo por tarea (judge, director, writer)
    style_id: int | None = None


@router.post("/videos/{video_id}/youtube")
def youtube(video_id: int, body: YoutubeIn):
    v = _video(video_id)
    if (v["duration"] or 0) < body.target_min * 60 * 1.3:
        raise HTTPException(400, f"El video dura {int((v['duration'] or 0) // 60)} min: para un resumen de "
                                 f"{int(body.target_min)} min necesita ser bastante más largo.")
    from ..ai import models
    models.check(body.llm_model)
    return jobs.enqueue("stream_youtube", video_id=video_id, params={"video_id": video_id, **body.model_dump()})


@router.get("/thumbs/{name}")
def thumb(name: str):
    path = (config.STUDIO_DIR / "thumbs" / name).resolve()
    if path.parent != (config.STUDIO_DIR / "thumbs").resolve() or not path.exists():
        raise HTTPException(404, "Miniatura no encontrada.")
    return FileResponse(path, media_type="image/jpeg")


jobs.HANDLERS["stream_youtube"] = stream.run_youtube
