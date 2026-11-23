"""Acceso a SQLite (librería estándar, sin ORM por ahora)."""
from __future__ import annotations

import json
import threading
import sqlite3
from contextlib import contextmanager
from typing import Any, Iterator

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS videos (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    title         TEXT NOT NULL,
    original_path TEXT NOT NULL,
    stored_path   TEXT NOT NULL UNIQUE,
    storage_mode  TEXT NOT NULL CHECK (storage_mode IN ('reference', 'copy')),
    size_bytes    INTEGER,
    duration      REAL,
    width         INTEGER,
    height        INTEGER,
    fps           REAL,
    video_codec   TEXT,
    audio_codec   TEXT,
    has_audio     INTEGER NOT NULL DEFAULT 0,
    thumbnail     TEXT,
    imported_at   TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

CREATE INDEX IF NOT EXISTS idx_videos_original ON videos (original_path);

CREATE TABLE IF NOT EXISTS jobs (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    kind       TEXT NOT NULL,
    video_id   INTEGER,
    clip_id    INTEGER,
    params     TEXT,
    status     TEXT NOT NULL CHECK (status IN ('queued','running','done','error','cancelled')),
    progress   REAL NOT NULL DEFAULT 0,
    stage      TEXT,
    error      TEXT,
    result     TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS transcripts (
    video_id   INTEGER PRIMARY KEY REFERENCES videos(id) ON DELETE CASCADE,
    language   TEXT,
    model      TEXT,
    device     TEXT,
    segments   TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS analyses (
    video_id   INTEGER PRIMARY KEY REFERENCES videos(id) ON DELETE CASCADE,
    method     TEXT NOT NULL,
    llm_model  TEXT,
    scenes     TEXT,
    energy     TEXT,
    params     TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS clips (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    video_id    INTEGER NOT NULL REFERENCES videos(id) ON DELETE CASCADE,
    start_s     REAL NOT NULL,
    end_s       REAL NOT NULL,
    title       TEXT NOT NULL,
    hook        TEXT,
    reason      TEXT,
    suggestions TEXT,
    scores      TEXT,
    score       REAL,
    status      TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','approved','discarded')),
    notes       TEXT,
    source      TEXT NOT NULL DEFAULT 'manual',
    judge       TEXT,
    created_at  TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    updated_at  TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
CREATE INDEX IF NOT EXISTS idx_clips_video ON clips (video_id);

CREATE TABLE IF NOT EXISTS renders (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    clip_id    INTEGER NOT NULL REFERENCES clips(id) ON DELETE CASCADE,
    path       TEXT NOT NULL,
    options    TEXT,
    duration   REAL,
    size_bytes INTEGER,
    encoder    TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

-- ---------- Studio (contenido original) ----------
CREATE TABLE IF NOT EXISTS studio_projects (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    name         TEXT NOT NULL,
    format       TEXT NOT NULL DEFAULT 'vertical' CHECK (format IN ('vertical','horizontal','square')),
    source_path  TEXT NOT NULL,
    source_kind  TEXT NOT NULL CHECK (source_kind IN ('video','audio')),
    source_meta  TEXT,
    transcript   TEXT,
    timeline     TEXT,
    style        TEXT,
    history_id   INTEGER,
    created_at   TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    updated_at   TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS studio_history (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL REFERENCES studio_projects(id) ON DELETE CASCADE,
    timeline   TEXT NOT NULL,
    style      TEXT NOT NULL,
    note       TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
CREATE INDEX IF NOT EXISTS idx_studio_history ON studio_history (project_id, id);

CREATE TABLE IF NOT EXISTS studio_renders (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL REFERENCES studio_projects(id) ON DELETE CASCADE,
    path       TEXT NOT NULL,
    quality    TEXT NOT NULL,
    duration   REAL,
    size_bytes INTEGER,
    encoder    TEXT,
    timeline_hash TEXT,
    qa         TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS assets (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    kind       TEXT NOT NULL CHECK (kind IN ('music','sfx','broll')),
    media_type TEXT NOT NULL CHECK (media_type IN ('audio','video','image')),
    path       TEXT NOT NULL UNIQUE,
    title      TEXT NOT NULL,
    tags       TEXT NOT NULL DEFAULT '',
    duration   REAL,
    builtin    INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
"""

DEFAULT_SETTINGS = {
    "whisper_model": "large-v3-turbo",
    "whisper_language": "es",
    "vocabulary": [],  # diccionario de nombres para Whisper (marcas, juegos, apodos)
    "vocab_fixes": {},  # correcciones aprendidas de tus subtítulos: {"neva": "Eneba"}
    "llm_backend": "llamacpp",
    "ollama_url": "http://127.0.0.1:11434",
    "ollama_model": "qwen3:14b",
    "judge_model": "",  # vacío = automático (un modelo distinto al que elige los momentos)
    "director_model": "",  # vacío = el mismo que analiza
    "writer_model": "",  # vacío = el mismo que analiza
}


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


@contextmanager
def get_conn() -> Iterator[sqlite3.Connection]:
    conn = connect()
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    config.ensure_dirs()
    with get_conn() as conn:
        conn.executescript(SCHEMA)
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(studio_renders)")}
        if "qa" not in cols:  # bases creadas antes del control de calidad
            conn.execute("ALTER TABLE studio_renders ADD COLUMN qa TEXT")
        if "judge" not in {r["name"] for r in conn.execute("PRAGMA table_info(clips)")}:
            conn.execute("ALTER TABLE clips ADD COLUMN judge TEXT")
    if get_setting("authorized_folders") is None:
        set_setting("authorized_folders", config.default_authorized_folders())
    for k, v in DEFAULT_SETTINGS.items():
        if get_setting(k) is None:
            set_setting(k, v)


_job_override = threading.local()


def set_job_model(model: str | None) -> None:
    """Modelo de IA elegido para el trabajo actual (hilo del worker); None = el de Configuración."""
    _job_override.model = model or None


# Un modelo distinto para cada tarea (como un equipo: quien elige momentos, quien juzga, quien edita, quien escribe)
ROLE_SETTINGS = {"analysis": "ollama_model", "judge": "judge_model", "director": "director_model",
                 "writer": "writer_model", "thumbnail": "thumbnail_model"}


def set_job_models(models: dict | None) -> None:
    """Modelos por tarea elegidos para el trabajo actual (p. ej. {"director": "gemma4:12b"})."""
    _job_override.roles = {k: v for k, v in (models or {}).items() if k in ROLE_SETTINGS and v} or None


def role_model(role: str) -> str | None:
    """Modelo para una tarea: el elegido para este trabajo, si no el de Configuración para esa tarea y, si está
    vacío, el modelo principal (el que analiza)."""
    roles = getattr(_job_override, "roles", None) or {}
    if roles.get(role):
        return roles[role]
    if role != "analysis":
        v = get_setting(ROLE_SETTINGS[role])
        if v and not _other_engine_model(v):
            return v
    return get_setting("ollama_model")


def _other_engine_model(name: str) -> bool:
    """Un modelo elegido para una tarea cuando el motor era llama.cpp (su alias, p. ej. «smc-qwen36») no existe en
    Ollama: si el motor actual es Ollama, esa tarea usa el modelo principal en vez de fallar."""
    if (get_setting("llm_backend") or "ollama") == "llamacpp":
        return False
    alias = get_setting("llamacpp_alias") or "smc-qwen36"
    return name == alias or name.startswith("smc-")


def get_setting(key: str, default: Any = None) -> Any:
    if key == "ollama_model" and getattr(_job_override, "model", None):
        return _job_override.model
    with get_conn() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return json.loads(row["value"]) if row else default


def set_setting(key: str, value: Any) -> None:
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, json.dumps(value)),
        )
