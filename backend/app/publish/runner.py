"""Publicador en segundo plano: independiente de la cola de la GPU (una subida no espera a que acabe un render).

Cada 20 s revisa las publicaciones «para ya» y las programadas cuya hora llegó. Si la PC estaba apagada a la
hora programada, se publican en cuanto se abre la app.
"""
from __future__ import annotations

import os
import threading
import time
import traceback
from pathlib import Path

from . import oauth, platforms, store

_thread: threading.Thread | None = None
_wake = threading.Event()
_lock = threading.Lock()


def publish_one(post: dict) -> dict:
    """Sube una publicación. Lanza excepción con mensaje claro si falla."""
    acc = store.get_account(post["account_id"]) if post.get("account_id") else None
    if not acc or acc["platform"] != post["platform"]:
        raise platforms.PublishError(f"No hay una cuenta de {store.PLATFORM_NAMES[post['platform']]} conectada.")
    path = Path(post["file_path"])
    if not path.exists():
        raise platforms.PublishError("El archivo del video ya no existe.")

    def progress(p: float, stage: str) -> None:
        store.update_post(post["id"], progress=round(max(0, min(1, p)) * 100, 1), stage=stage)

    return platforms.UPLOADERS[post["platform"]](acc, path, post["meta"], post["options"], progress)


def run_post(post_id: int) -> None:
    with _lock:
        post = store.get_post(post_id)
        if not post or post["status"] not in ("queued", "scheduled"):
            return
        store.update_post(post_id, status="publishing", stage="Iniciando", progress=0, error=None)
    try:
        res = publish_one(post)
        store.update_post(post_id, status="published", progress=100, stage="Publicado",
                          remote_id=res.get("remote_id"), remote_url=res.get("remote_url"), result=res,
                          published_at=store.now_str())
    except (platforms.PublishError, oauth.OAuthError) as exc:
        store.update_post(post_id, status="failed", stage="Error", error=str(exc)[:1000])
    except Exception as exc:  # noqa: BLE001 — se muestra en la interfaz
        traceback.print_exc()
        store.update_post(post_id, status="failed", stage="Error", error=f"Error inesperado: {exc}"[:1000])


def tick() -> int:
    n = 0
    for p in store.due_posts():
        run_post(p["id"])
        n += 1
    return n


def _loop() -> None:
    while True:
        try:
            tick()
        except Exception:  # noqa: BLE001
            traceback.print_exc()
        _wake.wait(20)
        _wake.clear()


def wake() -> None:
    if os.environ.get("SMC_JOBS_SYNC") == "1":
        tick()
    else:
        _wake.set()


def start() -> None:
    global _thread
    store.ensure_schema()
    store.reset_interrupted()
    if os.environ.get("SMC_JOBS_SYNC") == "1":
        return
    if _thread is None or not _thread.is_alive():
        _thread = threading.Thread(target=_loop, daemon=True, name="smc-publish")
        _thread.start()


def _sleep(s: float) -> None:  # para pruebas
    time.sleep(s)
