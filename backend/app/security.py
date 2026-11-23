"""Control de acceso a carpetas: la app solo puede ver lo que está dentro de las carpetas autorizadas."""
from __future__ import annotations

import os
from pathlib import Path

from fastapi import HTTPException

from . import db


def _norm(p: Path) -> str:
    return os.path.normcase(str(p.resolve()))


def is_within(path: Path, root: Path) -> bool:
    p, r = _norm(path), _norm(root)
    if p == r:
        return True
    r_prefix = r if r.endswith(os.sep) else r + os.sep
    return p.startswith(r_prefix)


def authorized_roots() -> list[Path]:
    folders = db.get_setting("authorized_folders", []) or []
    return [Path(f) for f in folders]


def root_for(path: Path) -> Path | None:
    for root in authorized_roots():
        if is_within(path, root):
            return root
    return None


def require_authorized(raw: str) -> Path:
    """Valida que la ruta exista y esté dentro de una carpeta autorizada."""
    if not raw:
        raise HTTPException(400, "Ruta vacía.")
    try:
        path = Path(raw).expanduser().resolve()
    except (OSError, RuntimeError):
        raise HTTPException(400, "Ruta inválida.")
    if root_for(path) is None:
        raise HTTPException(403, "La ruta está fuera de las carpetas autorizadas.")
    if not path.exists():
        raise HTTPException(404, "La ruta no existe.")
    return path
