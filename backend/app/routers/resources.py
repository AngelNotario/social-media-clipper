"""Índice de recursos encontrados en internet (para revisarlos, reutilizarlos o borrarlos)."""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Request

from .. import media
from ..resources import store
from ..streaming import serve_file

router = APIRouter(prefix="/resources", tags=["resources"])


@router.get("")
def list_resources(limit: int = 100, offset: int = 0):
    return {"items": store.index_list(max(1, min(limit, 500)), max(0, offset))}


@router.get("/search")
def search_resources(q: str, what: str | None = None, limit: int = 20):
    return {"items": store.index_search(q, what, max(1, min(limit, 100)))}


@router.delete("/{rid}")
def delete_resource(rid: int):
    if not store.index_delete(rid):
        raise HTTPException(404, "Ese recurso no está en el índice.")
    return {"ok": True}


@router.get("/{rid}/file")
def resource_file(rid: int, request: Request):
    r = store.get(rid)
    if not r:
        raise HTTPException(404, "Ese recurso no está en el índice.")
    p = Path(r["path"])
    return serve_file(p, request, media.guess_mime(p))
