"""Tablas y acceso a datos de la publicación en redes (cuentas conectadas y cola de publicaciones)."""
from __future__ import annotations

import json
from datetime import datetime

from .. import db

PLATFORMS = ("youtube", "tiktok", "instagram")
PLATFORM_NAMES = {"youtube": "YouTube", "tiktok": "TikTok", "instagram": "Instagram"}

# Credenciales de las apps de desarrollador del usuario (se guardan solo en su PC)
CRED_FIELDS = {
    "youtube": ("client_id", "client_secret"),
    "tiktok": ("client_key", "client_secret"),
    "instagram": ("app_id", "app_secret", "config_id"),
}
SECRET_FIELDS = {"client_secret", "app_secret"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS social_accounts (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    platform   TEXT NOT NULL,
    remote_id  TEXT NOT NULL,
    name       TEXT NOT NULL,
    avatar     TEXT,
    tokens     TEXT NOT NULL,
    extra      TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    UNIQUE (platform, remote_id)
);
CREATE TABLE IF NOT EXISTS publish_posts (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    batch        TEXT NOT NULL,
    platform     TEXT NOT NULL,
    account_id   INTEGER,
    source_kind  TEXT NOT NULL,
    source_id    INTEGER,
    file_path    TEXT NOT NULL,
    title        TEXT,
    meta         TEXT NOT NULL,
    options      TEXT NOT NULL,
    status       TEXT NOT NULL CHECK (status IN ('scheduled','queued','publishing','published','failed','cancelled')),
    scheduled_at TEXT,
    progress     REAL NOT NULL DEFAULT 0,
    stage        TEXT,
    error        TEXT,
    remote_id    TEXT,
    remote_url   TEXT,
    result       TEXT,
    published_at TEXT,
    created_at   TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    updated_at   TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
CREATE INDEX IF NOT EXISTS idx_publish_status ON publish_posts (status, scheduled_at);
"""


def ensure_schema() -> None:
    with db.get_conn() as conn:
        conn.executescript(SCHEMA)


def now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ---------------- credenciales ----------------

def get_creds(platform: str) -> dict:
    return db.get_setting(f"publish_{platform}_app", {}) or {}


def set_creds(platform: str, fields: dict) -> None:
    cur = get_creds(platform)
    for k in CRED_FIELDS[platform]:
        if k in fields and fields[k] is not None:
            v = str(fields[k]).strip()
            if k in SECRET_FIELDS and v and set(v) == {"•"}:  # valor enmascarado sin cambios
                continue
            cur[k] = v
    db.set_setting(f"publish_{platform}_app", cur)


def creds_public(platform: str) -> dict:
    c = get_creds(platform)
    out = {}
    for k in CRED_FIELDS[platform]:
        v = c.get(k) or ""
        out[k] = ("•" * 8 if v else "") if k in SECRET_FIELDS else v
    return out


def configured(platform: str) -> bool:
    c = get_creds(platform)
    need = [f for f in CRED_FIELDS[platform] if f != "config_id"]
    return all(c.get(f) for f in need)


# ---------------- cuentas ----------------

def _acc(row) -> dict:
    d = dict(row)
    d["tokens"] = json.loads(d["tokens"] or "{}")
    d["extra"] = json.loads(d["extra"] or "{}")
    return d


def save_account(platform: str, remote_id: str, name: str, tokens: dict, avatar: str | None = None,
                 extra: dict | None = None) -> int:
    with db.get_conn() as conn:
        conn.execute(
            "INSERT INTO social_accounts (platform, remote_id, name, avatar, tokens, extra) VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(platform, remote_id) DO UPDATE SET name = excluded.name, avatar = excluded.avatar, "
            "tokens = excluded.tokens, extra = excluded.extra, updated_at = datetime('now','localtime')",
            (platform, str(remote_id), name, avatar, json.dumps(tokens), json.dumps(extra or {})))
        return conn.execute("SELECT id FROM social_accounts WHERE platform = ? AND remote_id = ?",
                            (platform, str(remote_id))).fetchone()["id"]


def update_tokens(account_id: int, tokens: dict) -> None:
    with db.get_conn() as conn:
        conn.execute("UPDATE social_accounts SET tokens = ?, updated_at = datetime('now','localtime') WHERE id = ?",
                     (json.dumps(tokens), account_id))


def get_account(account_id: int) -> dict | None:
    with db.get_conn() as conn:
        r = conn.execute("SELECT * FROM social_accounts WHERE id = ?", (account_id,)).fetchone()
    return _acc(r) if r else None


def list_accounts(platform: str | None = None) -> list[dict]:
    sql, args = "SELECT * FROM social_accounts", ()
    if platform:
        sql, args = sql + " WHERE platform = ?", (platform,)
    with db.get_conn() as conn:
        return [_acc(r) for r in conn.execute(sql + " ORDER BY id", args)]


def account_public(a: dict) -> dict:
    exp = a["tokens"].get("refresh_expires_at") or a["tokens"].get("expires_at")
    return {"id": a["id"], "platform": a["platform"], "name": a["name"], "avatar": a.get("avatar"),
            "remote_id": a["remote_id"], "extra": {k: v for k, v in a["extra"].items() if k != "page_token"},
            "expires_at": exp, "connected_at": a["created_at"]}


def delete_account(account_id: int) -> None:
    with db.get_conn() as conn:
        conn.execute("DELETE FROM social_accounts WHERE id = ?", (account_id,))


# ---------------- publicaciones ----------------

def _post(row) -> dict:
    d = dict(row)
    for k in ("meta", "options", "result"):
        d[k] = json.loads(d[k]) if d.get(k) else ({} if k != "result" else None)
    return d


def create_post(**f) -> int:
    for k in ("meta", "options"):
        f[k] = json.dumps(f.get(k) or {}, ensure_ascii=False)
    cols = ", ".join(f)
    with db.get_conn() as conn:
        cur = conn.execute(f"INSERT INTO publish_posts ({cols}) VALUES ({', '.join('?' * len(f))})", tuple(f.values()))
        return cur.lastrowid


def update_post(post_id: int, **f) -> None:
    for k in ("meta", "options", "result"):
        if k in f and not isinstance(f[k], str) and f[k] is not None:
            f[k] = json.dumps(f[k], ensure_ascii=False)
    sets = ", ".join(f"{k} = ?" for k in f) + ", updated_at = datetime('now','localtime')"
    with db.get_conn() as conn:
        conn.execute(f"UPDATE publish_posts SET {sets} WHERE id = ?", (*f.values(), post_id))


def get_post(post_id: int) -> dict | None:
    with db.get_conn() as conn:
        r = conn.execute("SELECT * FROM publish_posts WHERE id = ?", (post_id,)).fetchone()
    return _post(r) if r else None


def list_posts(status: str | None = None, limit: int = 200) -> list[dict]:
    sql, args = "SELECT * FROM publish_posts", []
    if status:
        sql += " WHERE status = ?"
        args.append(status)
    sql += " ORDER BY CASE status WHEN 'publishing' THEN 0 WHEN 'queued' THEN 1 WHEN 'scheduled' THEN 2 ELSE 3 END, " \
           "COALESCE(scheduled_at, created_at) DESC, id DESC LIMIT ?"
    args.append(limit)
    with db.get_conn() as conn:
        return [_post(r) for r in conn.execute(sql, args)]


def due_posts() -> list[dict]:
    """Publicaciones listas para subir: las de 'ahora' y las programadas cuya hora ya llegó."""
    with db.get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM publish_posts WHERE status = 'queued' OR (status = 'scheduled' AND scheduled_at <= ?) "
            "ORDER BY COALESCE(scheduled_at, created_at), id", (now_str(),)).fetchall()
    return [_post(r) for r in rows]


def reset_interrupted() -> None:
    with db.get_conn() as conn:
        conn.execute("UPDATE publish_posts SET status = 'failed', stage = 'Error', "
                     "error = 'Interrumpido: la app se cerró durante la subida. Puedes reintentar.' WHERE status = 'publishing'")
