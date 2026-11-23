"""Cola de trabajos en segundo plano (un trabajo a la vez para no saturar la GPU)."""
from __future__ import annotations

import json
import os
import queue
import threading
import traceback
from typing import Callable

from . import db

_q: "queue.Queue[int]" = queue.Queue()
_worker: threading.Thread | None = None
HANDLERS: dict[str, Callable] = {}


class JobCancelled(Exception):
    pass


def _sync() -> bool:
    return os.environ.get("SMC_JOBS_SYNC") == "1"


def row_to_dict(row) -> dict:
    d = dict(row)
    for k in ("params", "result"):
        d[k] = json.loads(d[k]) if d.get(k) else None
    return d


def get(job_id: int) -> dict | None:
    with db.get_conn() as conn:
        row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    return row_to_dict(row) if row else None


def update(job_id: int, **fields) -> None:
    if "result" in fields:
        fields["result"] = json.dumps(fields["result"])
    sets = ", ".join(f"{k} = ?" for k in fields) + ", updated_at = datetime('now','localtime')"
    with db.get_conn() as conn:
        conn.execute(f"UPDATE jobs SET {sets} WHERE id = ?", (*fields.values(), job_id))


def active_for(video_id: int | None = None, clip_id: int | None = None, kind: str | None = None):
    sql = "SELECT * FROM jobs WHERE status IN ('queued','running')"
    params: list = []
    if video_id is not None:
        sql += " AND video_id = ?"
        params.append(video_id)
    if clip_id is not None:
        sql += " AND clip_id = ?"
        params.append(clip_id)
    if kind:
        sql += " AND kind = ?"
        params.append(kind)
    with db.get_conn() as conn:
        row = conn.execute(sql + " ORDER BY id LIMIT 1", params).fetchone()
    return row_to_dict(row) if row else None


def enqueue(kind: str, video_id: int | None = None, clip_id: int | None = None,
            params: dict | None = None) -> dict:
    with db.get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO jobs (kind, video_id, clip_id, params, status, stage) VALUES (?, ?, ?, ?, 'queued', 'En cola')",
            (kind, video_id, clip_id, json.dumps(params or {})),
        )
        job_id = cur.lastrowid
    if _sync():
        _run(job_id)
    else:
        _q.put(job_id)
    return get(job_id)


def cancel(job_id: int) -> None:
    job = get(job_id)
    if job and job["status"] in ("queued", "running"):
        update(job_id, status="cancelled", stage="Cancelado")


def _run(job_id: int) -> None:
    job = get(job_id)
    if not job or job["status"] != "queued":
        return
    update(job_id, status="running", stage="Iniciando", progress=0)

    def progress(p: float, stage: str | None = None) -> None:
        current = get(job_id)
        if current and current["status"] == "cancelled":
            raise JobCancelled()
        fields = {"progress": round(max(0.0, min(1.0, p)) * 100, 1)}
        if stage:
            fields["stage"] = stage
        update(job_id, **fields)

    db.set_job_model((job.get("params") or {}).get("llm_model"))
    db.set_job_models((job.get("params") or {}).get("models"))
    try:
        result = HANDLERS[job["kind"]](job, progress)
        if isinstance(result, dict) and (job.get("params") or {}).get("llm_model"):
            result.setdefault("llm_model", job["params"]["llm_model"])
        update(job_id, status="done", progress=100, stage="Completado", result=result or {})
    except JobCancelled:
        update(job_id, status="cancelled", stage="Cancelado")
    except Exception as exc:  # el error se muestra en la interfaz
        traceback.print_exc()
        update(job_id, status="error", stage="Error", error=str(exc)[:1000])
    finally:
        db.set_job_model(None)
        db.set_job_models(None)


def _loop() -> None:
    while True:
        job_id = _q.get()
        try:
            _run(job_id)
        finally:
            _q.task_done()


def start_worker() -> None:
    global _worker
    with db.get_conn() as conn:
        conn.execute("UPDATE jobs SET status='error', stage='Error', error='Interrumpido: la app se cerró durante el proceso.' "
                     "WHERE status = 'running'")
        pending = [r["id"] for r in conn.execute("SELECT id FROM jobs WHERE status='queued' ORDER BY id")]
    if _sync():
        return
    if _worker is None or not _worker.is_alive():
        _worker = threading.Thread(target=_loop, daemon=True, name="smc-jobs")
        _worker.start()
    for jid in pending:
        _q.put(jid)
