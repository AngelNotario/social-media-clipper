"""Índice de tus clips: la IA de visión mira fotogramas de tus videos y describe qué hay en cada tramo.

Con ese índice, el generador "video desde un tema" puede elegir los tramos que ilustran cada frase del guion.
"""
from __future__ import annotations

import base64
import json
import subprocess
from pathlib import Path
from typing import Callable

import numpy as np

from .. import config, db
from ..ai import llm

FOOTAGE_DIR = config.DATA_DIR / "footage"
SHOT_LEN = 6.0

SCHEMA = {
    "type": "object",
    "properties": {
        "caption": {"type": "string"},
        "tags": {"type": "array", "items": {"type": "string"}},
        "action": {"type": "integer"},
        "useful": {"type": "boolean"},
    },
    "required": ["caption", "tags", "action", "useful"],
}
PROMPT = ("Fotograma de un video. Responde JSON breve en español: caption = qué se ve en máx. 15 palabras "
          "(juego/app/lugar, personajes, acción); tags = 3 a 5 etiquetas; action = 0-100 cuánta acción sugiere; "
          "useful = false si es pantalla de carga, menú vacío, negro o sin interés visual.")

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS footage_index (
    video_id   INTEGER PRIMARY KEY REFERENCES videos(id) ON DELETE CASCADE,
    status     TEXT NOT NULL,
    shots      INTEGER NOT NULL DEFAULT 0,
    model      TEXT,
    indexed_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
CREATE TABLE IF NOT EXISTS footage_shots (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    video_id   INTEGER NOT NULL REFERENCES videos(id) ON DELETE CASCADE,
    start_s    REAL NOT NULL,
    end_s      REAL NOT NULL,
    caption    TEXT NOT NULL,
    tags       TEXT NOT NULL DEFAULT '',
    action     INTEGER NOT NULL DEFAULT 50,
    motion     REAL NOT NULL DEFAULT 0,
    talking    INTEGER NOT NULL DEFAULT 0,
    useful     INTEGER NOT NULL DEFAULT 1,
    thumb      TEXT
);
CREATE INDEX IF NOT EXISTS idx_footage_video ON footage_shots (video_id);
"""


def ensure_schema() -> None:
    with db.get_conn() as conn:
        conn.executescript(SCHEMA_SQL)


def vision_model() -> str:
    return db.get_setting("vision_model") or "qwen3.6:35b-a3b"


USELESS_WORDS = ("pantalla de carga", "cargando", "pantalla negra", "fondo negro", "imagen negra", "vacío", "vacia",
                 "en blanco", "sin contenido")


def is_useful(caption: str) -> bool:
    """El modelo de visión marca mal el campo 'useful'; se decide por la descripción."""
    c = caption.lower()
    return bool(c.strip()) and not any(w in c for w in USELESS_WORDS)


def _frame(src: Path, t: float, out: Path, width: int = 512) -> bool:
    r = subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", f"{t:.2f}", "-i", str(src), "-frames:v", "1",
                        "-vf", f"scale={width}:-2", "-q:v", "5", str(out)], capture_output=True, timeout=60)
    return r.returncode == 0 and out.exists()


def _motion(src: Path, t: float) -> float:
    """Diferencia media entre dos fotogramas separados 0.5 s (0-100)."""
    r = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{t:.2f}", "-i", str(src), "-t", "0.6",
                        "-vf", "fps=2,scale=96:54,format=gray", "-f", "rawvideo", "-"],
                       capture_output=True, timeout=60)
    data = np.frombuffer(r.stdout, dtype=np.uint8)
    n = 96 * 54
    if data.size < 2 * n:
        return 0.0
    a, b = data[:n].astype(np.float32), data[n:2 * n].astype(np.float32)
    return round(float(np.clip(np.mean(np.abs(a - b)) * 3, 0, 100)), 1)


def sample_times(duration: float) -> list[float]:
    n = int(max(3, min(30, duration / 40)))
    if duration < 8:
        return [duration / 2]
    margin = min(3.0, duration * 0.05)
    step = (duration - 2 * margin) / n
    return [round(margin + step * (i + 0.5), 2) for i in range(n)]


def index_video(video: dict, progress: Callable[[float, str], None]) -> int:
    ensure_schema()
    src = Path(video["stored_path"])
    dur = video["duration"] or 0
    vdir = FOOTAGE_DIR / f"video_{video['id']}"
    vdir.mkdir(parents=True, exist_ok=True)
    url, model = db.get_setting("ollama_url"), vision_model()
    times = sample_times(dur)
    rows = []
    for i, t in enumerate(times):
        progress(i / len(times), f"Mirando «{video['title'][:40]}» ({i + 1}/{len(times)})")
        img = vdir / f"shot_{i:03d}.jpg"
        if not _frame(src, t, img, width=448):
            continue
        b64 = base64.b64encode(img.read_bytes()).decode()
        try:
            d = llm.chat_json(url, model, "Eres un asistente de edición de video. Responde solo JSON.", PROMPT,
                              SCHEMA, images=[b64], num_ctx=4096, keep_alive="5m", timeout=300, max_tokens=220)
        except llm.LLMError:
            continue
        a = max(0.0, t - SHOT_LEN / 2)
        rows.append((video["id"], a, min(dur, a + SHOT_LEN), str(d.get("caption", ""))[:300],
                     " ".join(str(x).lower() for x in d.get("tags", []))[:200],
                     max(0, min(100, int(d.get("action", 50)))), _motion(src, t),
                     0, int(is_useful(str(d.get("caption", "")))), str(img)))
    with db.get_conn() as conn:
        conn.execute("DELETE FROM footage_shots WHERE video_id = ?", (video["id"],))
        conn.executemany(
            "INSERT INTO footage_shots (video_id, start_s, end_s, caption, tags, action, motion, talking, useful, thumb) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)
        conn.execute("INSERT OR REPLACE INTO footage_index (video_id, status, shots, model) VALUES (?, 'done', ?, ?)",
                     (video["id"], len(rows), model))
    return len(rows)


def run_index(job: dict, progress) -> dict:
    """Trabajo: indexa los videos indicados (o todos los no indexados)."""
    ensure_schema()
    ids = (job["params"] or {}).get("video_ids")
    force = (job["params"] or {}).get("force", False)
    with db.get_conn() as conn:
        vids = [dict(r) for r in conn.execute("SELECT * FROM videos ORDER BY id").fetchall()]
        done = {r["video_id"] for r in conn.execute("SELECT video_id FROM footage_index WHERE status='done'")}
    todo = [v for v in vids if (not ids or v["id"] in ids) and (force or v["id"] not in done)
            and Path(v["stored_path"]).exists()]
    if not todo:
        return {"videos": 0, "shots": 0}
    url, model = db.get_setting("ollama_url"), vision_model()
    st = llm.status(url)
    if not st["available"] or model not in st["models"]:
        raise RuntimeError(f"Necesito Ollama con el modelo de visión {model} para indexar tus clips.")
    total = 0
    try:
        for k, v in enumerate(todo):
            total += index_video(v, lambda p, s: progress((k + p) / len(todo), s))
    finally:
        llm.unload(url, model)  # libera la VRAM para Whisper / voz
    return {"videos": len(todo), "shots": total}


def catalog(video_ids: list[int] | None = None, limit: int = 220) -> list[dict]:
    ensure_schema()
    sql = ("SELECT s.*, v.title AS video_title FROM footage_shots s JOIN videos v ON v.id = s.video_id "
           "WHERE s.useful = 1")
    params: list = []
    if video_ids:
        sql += f" AND s.video_id IN ({','.join('?' * len(video_ids))})"
        params += list(video_ids)
    with db.get_conn() as conn:
        rows = [dict(r) for r in conn.execute(sql + " ORDER BY s.video_id, s.start_s", params).fetchall()]
    if len(rows) > limit:  # repartir de forma uniforme
        step = len(rows) / limit
        rows = [rows[int(i * step)] for i in range(limit)]
    return rows


def overview() -> list[dict]:
    ensure_schema()
    with db.get_conn() as conn:
        rows = conn.execute("""
            SELECT v.id, v.title, v.duration, v.has_audio, f.status, f.shots, f.indexed_at
            FROM videos v LEFT JOIN footage_index f ON f.video_id = v.id ORDER BY v.id DESC""").fetchall()
    return [dict(r) for r in rows]
