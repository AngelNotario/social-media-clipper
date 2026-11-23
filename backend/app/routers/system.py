"""Estado del sistema, configuración y estadísticas del dashboard."""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .. import config, db, media

router = APIRouter(tags=["system"])


@router.get("/health")
def health():
    return {"ok": True}


@router.get("/system/status")
def status():
    with db.get_conn() as conn:
        agg = conn.execute(
            "SELECT COUNT(*) AS n, COALESCE(SUM(duration),0) AS dur, COALESCE(SUM(size_bytes),0) AS size, "
            "SUM(CASE WHEN height > width THEN 1 ELSE 0 END) AS vertical, "
            "SUM(CASE WHEN width > height THEN 1 ELSE 0 END) AS horizontal "
            "FROM videos"
        ).fetchone()
        recent = conn.execute(
            "SELECT id, title, duration, width, height, thumbnail, imported_at "
            "FROM videos ORDER BY id DESC LIMIT 6"
        ).fetchall()
    return {
        "ffmpeg": {"available": media.ffmpeg_available(), "version": media.ffmpeg_version()},
        "data_dir": str(config.DATA_DIR),
        "authorized_folders": db.get_setting("authorized_folders", []),
        "library": {
            "videos": agg["n"],
            "total_duration": agg["dur"],
            "total_size": agg["size"],
            "vertical": agg["vertical"] or 0,
            "horizontal": agg["horizontal"] or 0,
        },
        "recent": [
            {**dict(r), "thumbnail_url": f"/api/library/{r['id']}/thumbnail" if r["thumbnail"] else None}
            for r in recent
        ],
    }


class SettingsUpdate(BaseModel):
    authorized_folders: list[str] = Field(max_length=50)


@router.get("/settings")
def get_settings():
    folders = db.get_setting("authorized_folders", [])
    return {
        "authorized_folders": [{"path": f, "exists": Path(f).is_dir()} for f in folders],
        "data_dir": str(config.DATA_DIR),
    }


@router.put("/settings")
def update_settings(req: SettingsUpdate):
    cleaned: list[str] = []
    for raw in req.authorized_folders:
        raw = raw.strip().strip('"')
        if not raw:
            continue
        p = Path(raw).expanduser()
        if not p.is_dir():
            raise HTTPException(400, f"La carpeta no existe: {raw}")
        resolved = str(p.resolve())
        if resolved not in cleaned:
            cleaned.append(resolved)
    db.set_setting("authorized_folders", cleaned)
    return get_settings()
