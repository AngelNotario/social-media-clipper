"""Biblioteca de recursos locales: música, efectos de sonido (SFX) y b-roll.

Incluye un pack de efectos ORIGINALES generados por la propia app con FFmpeg (sin derechos de terceros).
"""
from __future__ import annotations

import os
import re
import subprocess
import threading
from pathlib import Path

from .. import config, db, security
from .probe import ProbeError, probe_any

# Efectos sintetizados: (archivo, título, etiquetas, expresión lavfi)
STARTER_SFX = [
    ("whoosh.wav", "Whoosh", "whoosh transicion corte swoosh",
     "anoisesrc=d=0.55:c=pink:a=0.7,highpass=f=350,lowpass=f=5000,"
     "afade=t=in:d=0.22,afade=t=out:st=0.25:d=0.3"),
    ("whoosh_rapido.wav", "Whoosh rápido", "whoosh transicion rapido corte",
     "anoisesrc=d=0.3:c=pink:a=0.7,highpass=f=600,lowpass=f=7000,"
     "afade=t=in:d=0.1,afade=t=out:st=0.12:d=0.18"),
    ("pop.wav", "Pop", "pop texto aparece burbuja",
     "aevalsrc='0.8*sin(2*PI*(900-2600*t)*t)*exp(-28*t)':d=0.16:s=48000"),
    ("click.wav", "Click", "click tic interfaz",
     "aevalsrc='0.9*sin(2*PI*2200*t)*exp(-140*t)':d=0.06:s=48000"),
    ("impacto.wav", "Impacto", "impacto boom golpe enfasis",
     "aevalsrc='0.95*sin(2*PI*(90-70*t)*t)*exp(-5*t)':d=0.9:s=48000"),
    ("riser.wav", "Riser", "riser subida tension suspenso",
     "aevalsrc='0.45*sin(2*PI*(180+900*t*t)*t)*(t/1.6)':d=1.6:s=48000"),
    ("ding.wav", "Ding", "ding campana dato exito",
     "aevalsrc='0.55*sin(2*PI*1320*t)*exp(-4*t)+0.25*sin(2*PI*2640*t)*exp(-7*t)':d=1.2:s=48000"),
    ("bass_drop.wav", "Bass drop", "bassdrop drop grave impacto enfasis revelacion",
     "aevalsrc='0.95*sin(2*PI*(160*exp(-3*t))*t)*exp(-1.6*t)':d=1.8:s=48000"),
    ("glitch.wav", "Glitch", "glitch error digital transicion falla",
     "anoisesrc=d=0.35:c=white:a=0.5,acrusher=bits=4:samples=12:mix=1,"
     "volume='if(lt(mod(t,0.07),0.035),1,0.15)':eval=frame,afade=t=out:st=0.25:d=0.1"),
    ("obturador.wav", "Obturador de cámara", "camara foto obturador shutter captura",
     "aevalsrc='0.8*(random(0)*2-1)*exp(-60*t)+0.6*(random(1)*2-1)*exp(-60*(t-0.09))*gt(t,0.09)':d=0.25:s=48000"),
    ("swoosh_subida.wav", "Swoosh subida", "swoosh whoosh subida transicion entrada",
     "anoisesrc=d=0.5:c=pink:a=0.6,highpass=f=800,afade=t=in:d=0.4,afade=t=out:st=0.4:d=0.1"),
    ("riser_reverso.wav", "Riser reverso", "riser reverso suspenso antes revelacion tension",
     "aevalsrc='0.5*sin(2*PI*(900-700*t/1.2)*t)*(t/1.2)^2':d=1.2:s=48000"),
    ("latido.wav", "Latido", "latido corazon tension suspenso miedo",
     "aevalsrc='0.9*sin(2*PI*55*t)*(exp(-25*mod(t,0.8))+0.7*exp(-25*mod(t-0.2,0.8))*gt(mod(t,0.8),0.2))':d=1.6:s=48000"),
    ("tic_tac.wav", "Tic tac", "reloj tictac tiempo cuenta regresiva",
     "aevalsrc='0.7*sin(2*PI*(1800+600*gt(mod(t,1),0.5))*t)*exp(-90*mod(t,0.5))':d=2:s=48000"),
    ("error_buzz.wav", "Error", "error incorrecto buzz fallo no",
     "aevalsrc='0.5*sgn(sin(2*PI*110*t))*lt(t,0.18)+0.5*sgn(sin(2*PI*110*t))*between(t,0.24,0.5)':d=0.55:s=48000"),
    ("pop_burbuja.wav", "Pop burbuja", "pop burbuja aparece texto suave",
     "aevalsrc='0.7*sin(2*PI*(400+1800*t)*t)*exp(-35*t)':d=0.14:s=48000"),
]

# Carpetas propias del proyecto: data\media\sonidos (efectos) y data\media\musica (música)
PROJECT_MEDIA = (("sonidos", "sfx"), ("musica", "music"))


_pack_lock = threading.Lock()


def ensure_starter_pack() -> None:
    with _pack_lock:
        _ensure_starter_pack()


def _ensure_starter_pack() -> None:
    sfx_dir = config.ASSETS_DIR / "sfx"
    sfx_dir.mkdir(parents=True, exist_ok=True)
    for name, title, tags, expr in STARTER_SFX:
        out = sfx_dir / name
        if not out.exists():
            try:
                subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", expr,
                                "-ac", "2", "-ar", "48000", str(out)], capture_output=True, timeout=30)
            except Exception:
                continue
        if out.exists():
            _upsert(out, "sfx", "audio", title, tags, builtin=True)


def ensure_project_media() -> dict:
    """Indexa los audios nuevos de data\\media\\sonidos y data\\media\\musica (carpetas de la app)."""
    out = {}
    with _pack_lock:
        with db.get_conn() as conn:
            known = {r["path"] for r in conn.execute("SELECT path FROM assets").fetchall()}
        for sub, kind in PROJECT_MEDIA:
            folder = config.MEDIA_DIR / sub
            if not folder.is_dir():
                continue
            added = 0
            for p in folder.rglob("*"):
                if p.suffix.lower() not in config.AUDIO_EXTS or str(p) in known:
                    continue
                tags = _tags_from(p, folder)
                if kind == "sfx" and "kenney" in tags:
                    tags = tags.replace("kenney", "").strip()
                _upsert(p, kind, "audio", p.stem.replace("_", " ")[:120], tags)
                added += 1
            out[sub] = added
    return out


def _upsert(path: Path, kind: str, media_type: str, title: str, tags: str,
            duration: float | None = None, builtin: bool = False) -> int:
    with db.get_conn() as conn:
        row = conn.execute("SELECT id FROM assets WHERE path = ?", (str(path),)).fetchone()
        if row:
            return row["id"]
        if duration is None and media_type != "image":
            try:
                duration = probe_any(path)["duration"]
            except ProbeError:
                duration = None
        conn.execute(
            "INSERT OR IGNORE INTO assets (kind, media_type, path, title, tags, duration, builtin) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (kind, media_type, str(path), title, tags, duration, int(builtin)),
        )
        return conn.execute("SELECT id FROM assets WHERE path = ?", (str(path),)).fetchone()["id"]


def _tags_from(path: Path, root: Path) -> str:
    rel = path.relative_to(root) if security.is_within(path, root) else Path(path.name)
    words = re.split(r"[\s_\-./\\()\[\]]+", str(rel.with_suffix("")).lower())
    return " ".join(w for w in words if len(w) > 1 and not w.isdigit())


def scan_folder(folder: Path, forced_kind: str | None = None, limit: int = 3000) -> dict:
    added = skipped = 0
    for dirpath, dirnames, filenames in os.walk(folder):
        dirnames[:] = [d for d in dirnames if not d.startswith((".", "$"))]
        for fn in filenames:
            if added + skipped >= limit:
                break
            p = Path(dirpath) / fn
            ext = p.suffix.lower()
            if ext in config.AUDIO_EXTS:
                media_type = "audio"
            elif ext in config.VIDEO_EXTS:
                media_type = "video"
            elif ext in config.IMAGE_EXTS:
                media_type = "image"
            else:
                continue
            try:
                meta = probe_any(p) if media_type != "image" else {"duration": None}
            except ProbeError:
                skipped += 1
                continue
            if forced_kind:
                kind = forced_kind
            elif media_type == "audio":
                kind = "music" if (meta["duration"] or 0) >= 20 else "sfx"
            else:
                kind = "broll"
            if kind in ("music", "sfx") and media_type != "audio":
                skipped += 1
                continue
            if kind == "broll" and media_type == "audio":
                skipped += 1
                continue
            _upsert(p, kind, media_type, p.stem.replace("_", " ")[:120], _tags_from(p, folder), meta["duration"])
            added += 1
    return {"added": added, "skipped": skipped}


def row_to_dict(r) -> dict:
    d = dict(r)
    d["builtin"] = bool(d["builtin"])
    d["available"] = Path(d["path"]).exists()
    d["url"] = f"/api/studio/assets/{d['id']}/file"
    return d


def list_assets(kind: str | None = None) -> list[dict]:
    sql, params = "SELECT * FROM assets", []
    if kind:
        sql += " WHERE kind = ?"
        params.append(kind)
    with db.get_conn() as conn:
        rows = conn.execute(sql + " ORDER BY builtin DESC, kind, title COLLATE NOCASE", params).fetchall()
    return [row_to_dict(r) for r in rows]


def get_asset(asset_id: int) -> dict | None:
    with db.get_conn() as conn:
        r = conn.execute("SELECT * FROM assets WHERE id = ?", (asset_id,)).fetchone()
    return row_to_dict(r) if r else None


def find_by_tag(kind: str, tag: str) -> dict | None:
    for a in list_assets(kind):
        if tag in a["tags"].split() and a["available"]:
            return a
    return None


def catalog_for_llm(limit: int = 80) -> list[dict]:
    """Catálogo compacto para que la IA elija recursos."""
    out = []
    for a in list_assets():
        if not a["available"]:
            continue
        out.append({"id": a["id"], "kind": a["kind"], "title": a["title"], "tags": a["tags"],
                    "duration": round(a["duration"], 1) if a["duration"] else None,
                    "type": a["media_type"]})
        if len(out) >= limit:
            break
    return out
