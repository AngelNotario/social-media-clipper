"""Biblioteca en tiempo real: refleja lo que hay en tus carpetas autorizadas, como el explorador de archivos.

- Los videos nuevos que aparecen en una carpeta autorizada (o sus subcarpetas) se agregan solos.
- Un archivo que todavía se está escribiendo (p. ej. OBS grabando) espera a que deje de cambiar.
- Los videos que borraste o moviste dejan de mostrarse en Biblioteca y Stream (sus clips y proyectos se conservan;
  si el archivo vuelve, vuelve a aparecer).
La revisión corre en segundo plano y como mucho cada pocos segundos, así que abrir la Biblioteca nunca se traba.
"""
from __future__ import annotations

import os
import threading
import time
from pathlib import Path

from . import config, db, security

MAX_DEPTH = 4
SETTLE_S = 15.0  # un archivo que cambió hace menos de esto se considera "aún grabándose"
MIN_BYTES = 200_000
THROTTLE_S = 4.0

_lock = threading.Lock()
_state = {"last": 0.0, "running": False, "pending": 0, "added": 0}


def _key(p: str) -> str:
    return os.path.normcase(os.path.abspath(p))


def scan() -> list[Path]:
    """Videos terminados dentro de las carpetas autorizadas (recursivo, sin carpetas ocultas)."""
    out, now = [], time.time()
    for root in security.authorized_roots():
        base_depth = len(Path(root).parts)
        for dirpath, dirnames, filenames in os.walk(root):
            depth = len(Path(dirpath).parts) - base_depth
            dirnames[:] = [] if depth >= MAX_DEPTH else [
                d for d in dirnames if not d.startswith((".", "$")) and d.lower() not in ("node_modules",)]
            for f in filenames:
                p = Path(dirpath) / f
                if p.suffix.lower() not in config.VIDEO_EXTS:
                    continue
                try:
                    st = p.stat()
                except OSError:
                    continue
                if st.st_size >= MIN_BYTES and now - st.st_mtime >= SETTLE_S:
                    out.append(p)
    return out


def _known() -> set[str]:
    with db.get_conn() as conn:
        rows = conn.execute("SELECT original_path, stored_path FROM videos").fetchall()
    return {_key(r[k]) for r in rows for k in ("original_path", "stored_path") if r[k]}


def _run() -> None:
    from .routers.library import _import_one
    try:
        known = _known()
        new = [p for p in scan() if _key(str(p)) not in known]
        _state["pending"] = len(new)
        for p in sorted(new, key=lambda x: x.stat().st_mtime if x.exists() else 0, reverse=True):
            r = _import_one(str(p), "reference")
            if r.get("ok"):
                _state["added"] += 1
            _state["pending"] = max(0, _state["pending"] - 1)
    except Exception:  # noqa: BLE001 — la sincronización nunca tumba la app
        pass
    finally:
        _state["pending"] = 0
        _state["running"] = False


def kick() -> dict:
    """Lanza una revisión en segundo plano si toca (no bloquea). Devuelve el estado."""
    if os.environ.get("PYTEST_CURRENT_TEST") and not os.environ.get("SMC_SYNC_IN_TESTS"):
        return {"syncing": False, "pending": 0}  # en pruebas se llama a mano para que sea determinista
    with _lock:
        if not _state["running"] and time.time() - _state["last"] >= THROTTLE_S:
            _state["running"], _state["last"] = True, time.time()
            threading.Thread(target=_run, daemon=True, name="library-sync").start()
    return {"syncing": _state["running"], "pending": _state["pending"]}


def available(path: str | None) -> bool:
    return bool(path) and Path(path).exists()
