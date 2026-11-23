"""Índice de recursos encontrados en internet (para reutilizarlos en otras ediciones).

Cada recurso es un archivo real descargado (imagen o clip corto) registrado también como asset "broll"
de la biblioteca, con de dónde salió y qué tan bien lo calificó el verificador con visión.
"""
from __future__ import annotations

import re
import unicodedata
from pathlib import Path

from .. import config, db

WEB_DIR = config.DATA_DIR / "library" / "web"

SCHEMA = """
CREATE TABLE IF NOT EXISTS resource_index (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    asset_id       INTEGER NOT NULL,
    entity         TEXT NOT NULL,
    entity_norm    TEXT NOT NULL,
    what           TEXT NOT NULL,
    query          TEXT,
    source_url     TEXT,
    page_url       TEXT,
    title          TEXT,
    width          INTEGER,
    height         INTEGER,
    duration       REAL,
    verified_score REAL,
    used_count     INTEGER NOT NULL DEFAULT 0,
    last_used      TEXT,
    created_at     TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    license_note   TEXT,
    bg_color       TEXT
);
CREATE INDEX IF NOT EXISTS idx_resource_entity ON resource_index (entity_norm);
CREATE INDEX IF NOT EXISTS idx_resource_asset ON resource_index (asset_id);
"""

_ready = False

# Números dichos en palabras / romanos → dígitos ("GTA seis", "GTA VI" = "gta 6")
NUMS = {"cero": "0", "uno": "1", "una": "1", "dos": "2", "tres": "3", "cuatro": "4", "cinco": "5", "seis": "6",
        "siete": "7", "ocho": "8", "nueve": "9", "diez": "10", "one": "1", "two": "2", "three": "3", "four": "4",
        "five": "5", "six": "6", "seven": "7", "eight": "8", "nine": "9", "ten": "10",
        "ii": "2", "iii": "3", "iv": "4", "vi": "6", "vii": "7", "viii": "8", "ix": "9"}
STOP = {"de", "del", "la", "el", "los", "las", "un", "una", "y", "en", "the", "of", "a", "and", "para", "con", "por",
        "su", "sus", "lo", "al", "es", "que", "se", "mi", "tu", "o"}
VISUAL_IMAGE = ("logo", "producto", "captura_web", "noticia", "persona", "lugar")
VISUAL_VIDEO = ("clip", "juego")
WHATS = VISUAL_IMAGE + VISUAL_VIDEO + ("texto_clave",)


def ensure_schema() -> None:
    global _ready
    if _ready:
        return
    with db.get_conn() as conn:
        conn.executescript(SCHEMA)
    _ready = True


def norm(text: str) -> str:
    """Minúsculas, sin acentos ni signos; números en palabras/romanos → dígitos."""
    t = unicodedata.normalize("NFKD", str(text or "")).encode("ascii", "ignore").decode().lower()
    t = re.sub(r"[^a-z0-9]+", " ", t)
    t = re.sub(r"(?<=[a-z])(?=\d)|(?<=\d)(?=[a-z])", " ", t)  # gta6 → gta 6
    return " ".join(NUMS.get(w, w) for w in t.split())


def tokens(text: str) -> list[str]:
    return [w for w in norm(text).split() if w not in STOP and (len(w) > 1 or w.isdigit())]


def _tok_in(tok: str, words: set[str]) -> bool:
    if tok in words:
        return True
    if len(tok) >= 4:  # "códigos" ~ "codigo", "Enebas" ~ "eneba"
        return any(w.startswith(tok[:max(4, len(tok) - 2)]) or tok.startswith(w) and len(w) >= 4 for w in words)
    return False


def mentions(entity: str, text: str) -> bool:
    """¿Se menciona la entidad en el texto? El token principal (el más largo) debe aparecer y al menos la mitad
    de los tokens importantes."""
    et = tokens(entity)
    if not et:
        return False
    words = set(norm(text).split())
    main = max(et, key=len)
    if not _tok_in(main, words):
        return False
    hit = sum(1 for t in et if _tok_in(t, words))
    return hit * 2 >= len(et)


def _row(r) -> dict:
    d = dict(r)
    d["url"] = f"/api/resources/{d['id']}/file"
    return d


def add(asset_id: int, entity: str, what: str, query: str, source_url: str, page_url: str, title: str,
        width: int | None, height: int | None, duration: float | None, score: float | None,
        license_note: str = "", bg_color: str | None = None) -> int:
    ensure_schema()
    with db.get_conn() as conn:
        old = conn.execute("SELECT id FROM resource_index WHERE asset_id = ?", (asset_id,)).fetchone()
        if old:
            return old["id"]
        return conn.execute(
            "INSERT INTO resource_index (asset_id, entity, entity_norm, what, query, source_url, page_url, title, "
            "width, height, duration, verified_score, license_note, bg_color) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (asset_id, entity, norm(entity), what, query, source_url, page_url, (title or "")[:200], width, height,
             duration, score, license_note, bg_color)).lastrowid


def by_source(source_url: str) -> dict | None:
    ensure_schema()
    with db.get_conn() as conn:
        r = conn.execute("SELECT r.*, a.path, a.media_type FROM resource_index r JOIN assets a ON a.id = r.asset_id "
                         "WHERE r.source_url = ?", (source_url,)).fetchone()
    return _row(r) if r else None


def touch(rid: int) -> None:
    ensure_schema()
    with db.get_conn() as conn:
        conn.execute("UPDATE resource_index SET used_count = used_count + 1, last_used = datetime('now','localtime') "
                     "WHERE id = ?", (rid,))


def index_list(limit: int = 100, offset: int = 0) -> list[dict]:
    ensure_schema()
    with db.get_conn() as conn:
        rows = conn.execute(
            "SELECT r.*, a.path, a.media_type, a.title AS asset_title FROM resource_index r "
            "JOIN assets a ON a.id = r.asset_id ORDER BY r.id DESC LIMIT ? OFFSET ?", (limit, offset)).fetchall()
    out = []
    for r in rows:
        d = _row(r)
        d["available"] = Path(d["path"]).exists()
        out.append(d)
    return out


def index_search(text: str, what: str | None = None, limit: int = 20) -> list[dict]:
    """Recursos cuyo nombre/consulta/título coincide con el texto (sin importar acentos ni mayúsculas).
    Orden: más coincidencias, mejor calificación, más usados."""
    ensure_schema()
    q = tokens(text)
    if not q:
        return []
    with db.get_conn() as conn:
        rows = conn.execute("SELECT r.*, a.path, a.media_type, a.tags FROM resource_index r "
                            "JOIN assets a ON a.id = r.asset_id").fetchall()
    scored = []
    for r in rows:
        d = _row(r)
        if what and d["what"] != what:
            continue
        hay = set(norm(f"{d['entity']} {d.get('query') or ''} {d.get('title') or ''} {d.get('tags') or ''}").split())
        ent = set(d["entity_norm"].split())
        hits = sum(1 for t in q if _tok_in(t, hay))
        if not hits:
            continue
        exact = 2 if set(q) <= ent or ent <= set(q) else 0
        d["available"] = Path(d["path"]).exists()
        if not d["available"]:
            continue
        scored.append((hits + exact, d["verified_score"] or 0, d["used_count"], d))
    scored.sort(key=lambda x: (-x[0], -x[1], -x[2]))
    return [x[3] for x in scored[:limit]]


def get(rid: int) -> dict | None:
    ensure_schema()
    with db.get_conn() as conn:
        r = conn.execute("SELECT r.*, a.path, a.media_type FROM resource_index r JOIN assets a ON a.id = r.asset_id "
                         "WHERE r.id = ?", (rid,)).fetchone()
    return _row(r) if r else None


def index_delete(rid: int) -> bool:
    """Borra el recurso del índice; si el archivo es de la carpeta de recursos web, también el archivo y su asset."""
    ensure_schema()
    r = get(rid)
    if not r:
        return False
    p = Path(r["path"])
    with db.get_conn() as conn:
        conn.execute("DELETE FROM resource_index WHERE id = ?", (rid,))
        try:
            inside = p.resolve().is_relative_to(WEB_DIR.resolve())
        except (OSError, ValueError):
            inside = False
        if inside:
            conn.execute("DELETE FROM assets WHERE id = ?", (r["asset_id"],))
    if inside:
        p.unlink(missing_ok=True)
    return True
