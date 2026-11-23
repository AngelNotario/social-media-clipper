"""Persistencia de proyectos del Studio con historial (deshacer / rehacer)."""
from __future__ import annotations

import json
from pathlib import Path

from .. import db
from . import style as style_mod
from . import timeline as tlm

MAX_HISTORY = 60


def _load_row(project_id: int):
    with db.get_conn() as conn:
        return conn.execute("SELECT * FROM studio_projects WHERE id = ?", (project_id,)).fetchone()


def get(project_id: int) -> dict | None:
    r = _load_row(project_id)
    if not r:
        return None
    d = dict(r)
    d["source_meta"] = json.loads(d["source_meta"] or "{}")
    d["transcript"] = json.loads(d["transcript"]) if d["transcript"] else None
    d["timeline"] = json.loads(d["timeline"]) if d["timeline"] else tlm.empty(d["format"])
    d["style"] = style_mod.clean_style(json.loads(d["style"]) if d["style"] else None)
    return d


def raw_words(p: dict) -> list[dict]:
    if not p.get("transcript"):
        return []
    return [w for s in p["transcript"]["segments"] for w in (s.get("words") or [])]


def source_words(p: dict) -> list[dict]:
    """Palabras de la toma con el diccionario de nombres aplicado («Neva» → «Eneba»)."""
    from ..ai import vocab
    return vocab.apply(raw_words(p))


def public(p: dict) -> dict:
    """Vista para la API (sin la transcripción completa)."""
    with db.get_conn() as conn:
        h = conn.execute("SELECT id FROM studio_history WHERE project_id = ? ORDER BY id", (p["id"],)).fetchall()
        last = conn.execute("SELECT * FROM studio_renders WHERE project_id = ? ORDER BY id DESC LIMIT 1",
                            (p["id"],)).fetchone()
    ids = [r["id"] for r in h]
    pos = ids.index(p["history_id"]) if p["history_id"] in ids else len(ids) - 1
    out = {k: v for k, v in p.items() if k != "transcript"}
    out["has_transcript"] = p.get("transcript") is not None
    out["can_undo"] = pos > 0
    out["can_redo"] = 0 <= pos < len(ids) - 1
    out["timeline_hash"] = tlm.thash(p["timeline"], p["style"])
    out["last_render"] = render_dict(last) if last else None
    out["source_available"] = Path(p["source_path"]).exists()
    return out


def render_dict(r) -> dict:
    d = dict(r)
    d["available"] = Path(d["path"]).exists()
    d["url"] = f"/api/studio/renders/{d['id']}/file"
    extra = json.loads(d.get("qa") or "null") or {}
    d["qa"], d["audio"], d["stems"] = extra.get("qa"), extra.get("audio"), extra.get("stems")
    return d


def save(project_id: int, timeline: dict | None = None, style: dict | None = None,
         note: str = "", record: bool = True) -> dict:
    p = get(project_id)
    tl = timeline if timeline is not None else p["timeline"]
    st = style_mod.clean_style(style if style is not None else p["style"])
    new_fixes = [f for f in (tl.get("word_fixes") or []) if f not in (p["timeline"].get("word_fixes") or [])]
    if new_fixes:  # lo que corriges a mano en los subtítulos se aprende para los próximos videos
        try:
            from ..ai import vocab
            vocab.learn_from_fixes(raw_words(p), new_fixes)
        except Exception:  # noqa: BLE001 — aprender nunca impide guardar
            pass
    tlm.relayout(tl, source_words(p), p["source_meta"].get("duration") or 0)
    tl_json, st_json = json.dumps(tl, ensure_ascii=False), json.dumps(st)
    with db.get_conn() as conn:
        hist_id = p["history_id"]
        if record:
            if hist_id is not None:  # descartar la rama de "rehacer"
                conn.execute("DELETE FROM studio_history WHERE project_id = ? AND id > ?", (project_id, hist_id))
            cur = conn.execute("INSERT INTO studio_history (project_id, timeline, style, note) VALUES (?, ?, ?, ?)",
                               (project_id, tl_json, st_json, note[:200]))
            hist_id = cur.lastrowid
            conn.execute(
                "DELETE FROM studio_history WHERE project_id = ? AND id NOT IN "
                "(SELECT id FROM studio_history WHERE project_id = ? ORDER BY id DESC LIMIT ?)",
                (project_id, project_id, MAX_HISTORY))
        conn.execute(
            "UPDATE studio_projects SET timeline = ?, style = ?, history_id = ?, "
            "updated_at = datetime('now','localtime') WHERE id = ?",
            (tl_json, st_json, hist_id, project_id))
    return get(project_id)


def step(project_id: int, direction: int) -> dict:
    """direction = -1 deshacer, +1 rehacer."""
    p = get(project_id)
    with db.get_conn() as conn:
        rows = conn.execute("SELECT * FROM studio_history WHERE project_id = ? ORDER BY id", (project_id,)).fetchall()
        ids = [r["id"] for r in rows]
        if not ids:
            return p
        pos = ids.index(p["history_id"]) if p["history_id"] in ids else len(ids) - 1
        new = pos + direction
        if not 0 <= new < len(ids):
            return p
        r = rows[new]
        conn.execute("UPDATE studio_projects SET timeline = ?, style = ?, history_id = ?, "
                     "updated_at = datetime('now','localtime') WHERE id = ?",
                     (r["timeline"], r["style"], r["id"], project_id))
    return get(project_id)
