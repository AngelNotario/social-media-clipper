"""Qué video se publica: un clip exportado (renders) o un video final del Studio (studio_renders)."""
from __future__ import annotations

import json
from pathlib import Path

from .. import db, media


class SourceError(Exception):
    pass


def _text(segments: list[dict], a: float | None = None, b: float | None = None,
          ranges: list[tuple[float, float]] | None = None) -> str:
    """Lo que se dice dentro del tramo (o de los tramos del timeline), no en todo el video original."""
    if a is not None:
        ranges = [(a, b)]
    out = []
    for s in segments or []:
        if ranges and not any(s.get("end", 0) > x and s.get("start", 0) < y for x, y in ranges):
            continue
        out.append(str(s.get("text", "")).strip())
    return " ".join(x for x in out if x)


def resolve(kind: str, sid: int) -> dict:
    """→ {kind, id, path, title, hook, reason, text, duration, width, height, vertical, project_name}"""
    with db.get_conn() as conn:
        if kind == "clip_render":
            r = conn.execute("""SELECT r.path, c.title, c.hook, c.reason, c.start_s, c.end_s, c.video_id, v.title AS vtitle
                                FROM renders r JOIN clips c ON c.id = r.clip_id JOIN videos v ON v.id = c.video_id
                                WHERE r.id = ?""", (sid,)).fetchone()
            if not r:
                raise SourceError("Exportación no encontrada.")
            t = conn.execute("SELECT segments FROM transcripts WHERE video_id = ?", (r["video_id"],)).fetchone()
            text = _text(json.loads(t["segments"]) if t else [], r["start_s"], r["end_s"])
            info = {"title": r["title"], "hook": r["hook"] or "", "reason": r["reason"] or "", "text": text,
                    "origin": r["vtitle"], "video_id": r["video_id"]}
        elif kind == "studio_render":
            r = conn.execute("""SELECT r.path, r.project_id, p.name, p.transcript, p.timeline, p.source_meta FROM studio_renders r
                                JOIN studio_projects p ON p.id = r.project_id WHERE r.id = ?""", (sid,)).fetchone()
            if not r:
                raise SourceError("Video del Studio no encontrado.")
            tr = json.loads(r["transcript"]) if r["transcript"] else {}
            tl = json.loads(r["timeline"]) if r["timeline"] else {}
            ranges = [(float(s["src_in"]), float(s["src_out"])) for s in (tl.get("tracks") or {}).get("video") or []
                      if "src_in" in s]
            name = r["name"]
            for pre in ("Clip: ", "YouTube: ", "Tema: "):
                name = name[len(pre):] if name.startswith(pre) else name
            sm = json.loads(r["source_meta"]) if r["source_meta"] else {}
            clip = None
            if sm.get("clip_id"):
                clip = conn.execute("SELECT hook, reason FROM clips WHERE id = ?", (sm["clip_id"],)).fetchone()
            elif r["name"].startswith("Clip: "):
                clip = conn.execute("SELECT hook, reason FROM clips WHERE title = ? ORDER BY id DESC LIMIT 1",
                                    (name,)).fetchone()
            words = tl.get("words") or []  # lo que se dice en la edición, con tus correcciones de subtítulos
            text = " ".join(w["word"] for w in words) if words else _text(tr.get("segments") or [], ranges=ranges or None)
            info = {"title": name, "hook": (clip["hook"] if clip else "") or "", "reason": (clip["reason"] if clip else "") or "",
                    "text": text[:20000], "origin": r["name"],
                    "understood": sm.get("understood"), "research": sm.get("research"), "project_id": r["project_id"]}
        else:
            raise SourceError("Tipo de video no soportado.")
    path = Path(r["path"])
    if not path.exists():
        raise SourceError("El archivo exportado ya no existe en el disco.")
    p = media.probe(path)
    w, h = p.get("width") or 0, p.get("height") or 0
    return {"kind": kind, "id": sid, "path": str(path), **info, "duration": p.get("duration") or 0,
            "width": w, "height": h, "vertical": bool(h and w and h > w)}
