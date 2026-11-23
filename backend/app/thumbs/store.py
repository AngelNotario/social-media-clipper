"""Miniaturas guardadas (variantes por lote) y subidas de ejemplos/recursos."""
from __future__ import annotations

import json
from pathlib import Path

from .. import config, db

UPLOADS = config.DATA_DIR / "thumbs" / "uploads"


class ThumbError(Exception):
    pass


SCHEMA = """
CREATE TABLE IF NOT EXISTS thumbnails (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    batch       TEXT NOT NULL,
    source_kind TEXT NOT NULL,
    source_id   INTEGER NOT NULL,
    concept     TEXT NOT NULL,
    path        TEXT NOT NULL,
    score       REAL,
    critique    TEXT,
    detail      TEXT,
    info        TEXT,
    chosen      INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
CREATE INDEX IF NOT EXISTS idx_thumbs_src ON thumbnails (source_kind, source_id);
CREATE TABLE IF NOT EXISTS thumb_assets (
    batch TEXT NOT NULL, key TEXT NOT NULL, title TEXT, image TEXT, page TEXT, source TEXT
);
"""


def ensure_schema() -> None:
    with db.get_conn() as conn:
        conn.executescript(SCHEMA)


def save(batch, kind, sid, concept, path: Path, score, critique, detail, info) -> int:
    ensure_schema()
    with db.get_conn() as conn:
        return conn.execute(
            "INSERT INTO thumbnails (batch, source_kind, source_id, concept, path, score, critique, detail, info) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (batch, kind, sid, json.dumps(concept, ensure_ascii=False), str(path), score, critique,
             json.dumps(detail or {}), json.dumps(info or {}, ensure_ascii=False))).lastrowid


def add_asset_source(batch: str, key: str, f: dict) -> None:
    ensure_schema()
    with db.get_conn() as conn:
        conn.execute("INSERT INTO thumb_assets (batch, key, title, image, page, source) VALUES (?, ?, ?, ?, ?, ?)",
                     (batch, key, f.get("title"), f.get("image"), f.get("url"), f.get("source")))


def _row(r) -> dict:
    d = dict(r)
    for k in ("concept", "detail", "info"):
        d[k] = json.loads(d[k]) if d.get(k) else {}
    d["url"] = f"/api/thumbs/{d['id']}/file"
    d["exists"] = Path(d["path"]).exists()
    return d


def get(tid: int) -> dict | None:
    ensure_schema()
    with db.get_conn() as conn:
        r = conn.execute("SELECT * FROM thumbnails WHERE id = ?", (tid,)).fetchone()
    return _row(r) if r else None


def related(kind: str, sid: int) -> list[tuple[str, int]]:
    """El mismo video visto desde varios lados: un proyecto del Studio y sus exportaciones, un clip exportado y su
    video de origen. Así una miniatura hecha para el proyecto aparece al publicar su render (y al revés)."""
    keys = {(kind, int(sid))}
    with db.get_conn() as conn:
        def project_side(pid):
            keys.add(("project", int(pid)))
            for x in conn.execute("SELECT id FROM studio_renders WHERE project_id = ?", (pid,)):
                keys.add(("studio_render", x["id"]))
            row = conn.execute("SELECT source_meta FROM studio_projects WHERE id = ?", (pid,)).fetchone()
            try:
                sm = json.loads(row["source_meta"] or "{}") if row else {}
            except ValueError:
                sm = {}
            for k in ("video_id", "source_video_id"):
                if isinstance(sm.get(k), int):
                    keys.add(("video", sm[k]))
        if kind == "studio_render":
            r = conn.execute("SELECT project_id FROM studio_renders WHERE id = ?", (sid,)).fetchone()
            if r:
                project_side(r["project_id"])
        elif kind == "project":
            project_side(sid)
        elif kind == "clip_render":
            r = conn.execute("SELECT c.id AS cid FROM renders r JOIN clips c ON c.id = r.clip_id WHERE r.id = ?",
                             (sid,)).fetchone()
            if r:  # (el video completo de origen no: su miniatura no sirve para un clip)
                for x in conn.execute("SELECT id FROM renders WHERE clip_id = ?", (r["cid"],)):
                    keys.add(("clip_render", x["id"]))
    return sorted(keys)


def list_for(kind: str | None = None, sid: int | None = None, limit: int = 60, linked: bool = True) -> list[dict]:
    ensure_schema()
    sql, args = "SELECT * FROM thumbnails", []
    if kind:
        keys = related(kind, sid) if linked else [(kind, sid)]
        sql += " WHERE (" + " OR ".join("(source_kind = ? AND source_id = ?)" for _ in keys) + ")"
        for k, i in keys:
            args += [k, i]
    sql += " ORDER BY id DESC LIMIT ?"
    args.append(limit)
    with db.get_conn() as conn:
        rows = [_row(r) for r in conn.execute(sql, args)]
        srcs = {}
        for r in conn.execute("SELECT * FROM thumb_assets WHERE batch IN (%s)" % ",".join("?" * len({x["batch"] for x in rows}) or "''"),
                              list({x["batch"] for x in rows})):
            srcs.setdefault(r["batch"], []).append(dict(r))
    for r in rows:
        r["web_assets"] = srcs.get(r["batch"], [])
    return rows


def update(tid: int, **f) -> None:
    for k in ("concept",):
        if k in f and not isinstance(f[k], str):
            f[k] = json.dumps(f[k], ensure_ascii=False)
    sets = ", ".join(f"{k} = ?" for k in f)
    with db.get_conn() as conn:
        conn.execute(f"UPDATE thumbnails SET {sets} WHERE id = ?", (*f.values(), tid))


def choose(tid: int) -> None:
    t = get(tid)
    if not t:
        raise ThumbError("Miniatura no encontrada.")
    with db.get_conn() as conn:
        keys = related(t["source_kind"], t["source_id"])  # una sola elegida para todo el "mismo video"
        conn.execute("UPDATE thumbnails SET chosen = 0 WHERE " + " OR ".join("(source_kind = ? AND source_id = ?)" for _ in keys),
                     [v for k in keys for v in k])
        conn.execute("UPDATE thumbnails SET chosen = 1 WHERE id = ?", (tid,))


def chosen_for(kind: str, sid: int) -> dict | None:
    ensure_schema()
    with db.get_conn() as conn:
        keys = related(kind, sid)
        r = conn.execute("SELECT * FROM thumbnails WHERE (" + " OR ".join("(source_kind = ? AND source_id = ?)" for _ in keys)
                         + ") ORDER BY chosen DESC, score DESC, id DESC LIMIT 1",
                         [v for k in keys for v in k]).fetchone()
    return _row(r) if r else None
