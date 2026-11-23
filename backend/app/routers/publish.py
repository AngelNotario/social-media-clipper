"""Publicación en redes: credenciales, conexión de cuentas, metadatos con IA y cola programada."""
from __future__ import annotations

import html
import secrets
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from .. import db
from ..publish import metadata, oauth, runner, sources, store

router = APIRouter(prefix="/publish", tags=["publish"])

Platform = Literal["youtube", "tiktok", "instagram"]


def _base(request: Request) -> str:
    return str(request.base_url)


@router.get("/status")
def status(request: Request):
    out = {}
    for p in store.PLATFORMS:
        out[p] = {"name": store.PLATFORM_NAMES[p], "configured": store.configured(p), "creds": store.creds_public(p),
                  "redirect_uri": oauth.redirect_uri(_base(request), p),
                  "accounts": [store.account_public(a) for a in store.list_accounts(p)]}
    return {"platforms": out, "profile": metadata.profile(), "graph_version": oauth.graph_version()}


@router.put("/credentials/{platform}")
def set_credentials(platform: Platform, fields: dict[str, Any]):
    store.set_creds(platform, fields)
    return {"configured": store.configured(platform), "creds": store.creds_public(platform)}


@router.get("/oauth/{platform}/start")
def oauth_start(platform: Platform, request: Request):
    try:
        return {"url": oauth.start(platform, _base(request))}
    except oauth.OAuthError as exc:
        raise HTTPException(400, str(exc)) from exc


def _page(ok: bool, msg: str) -> HTMLResponse:
    color = "#34d399" if ok else "#f87171"
    body = f"""<!doctype html><meta charset="utf-8"><title>SOCIAL MEDIA CLIPPER</title>
<body style="font-family:system-ui;background:#0f121a;color:#e8ebf2;display:grid;place-items:center;height:100vh;margin:0">
<div style="max-width:520px;text-align:center"><h2 style="color:{color}">{'Cuenta conectada' if ok else 'No se pudo conectar'}</h2>
<p>{html.escape(msg)}</p><p><a style="color:#9d85ff" href="/#/publish">Volver a la app</a></p></div>
{'<script>setTimeout(()=>location.href="/#/publish?connected=1",1800)</script>' if ok else ''}</body>"""
    return HTMLResponse(body, status_code=200 if ok else 400)


@router.get("/oauth/{platform}/callback", response_class=HTMLResponse)
@router.get("/oauth/{platform}/callback/", response_class=HTMLResponse, include_in_schema=False)
def oauth_callback(platform: Platform, state: str = "", code: str = "", error: str = "", error_description: str = "",
                   error_reason: str = ""):
    if error or not code:
        return _page(False, error_description or error_reason or error or "La red no devolvió el permiso.")
    try:
        res = oauth.finish(state, code)
    except oauth.OAuthError as exc:
        return _page(False, str(exc))
    return _page(True, f"{store.PLATFORM_NAMES[res['platform']]}: {', '.join(n for n in res['accounts'] if n)}")


@router.delete("/accounts/{account_id}")
def remove_account(account_id: int):
    store.delete_account(account_id)
    return {"ok": True}


class Profile(BaseModel):
    niche: str = ""
    audience: str = ""
    brand_hashtags: str = ""
    cta: str = ""
    language: str = "es"
    tone: str = ""


@router.put("/profile")
def save_profile(p: Profile):
    db.set_setting("publish_profile", p.model_dump())
    return metadata.profile()


def _source(kind: str, sid: int) -> dict:
    try:
        return sources.resolve(kind, sid)
    except sources.SourceError as exc:
        raise HTTPException(404, str(exc)) from exc


def _summary(src: dict) -> dict:
    return {k: src.get(k) for k in ("kind", "id", "title", "hook", "duration", "width", "height", "vertical", "origin")}


class MetaReq(BaseModel):
    source_kind: Literal["clip_render", "studio_render"]
    source_id: int
    notes: str = ""


@router.post("/metadata")
def gen_metadata(req: MetaReq):
    src = _source(req.source_kind, req.source_id)
    meta = metadata.generate(src, req.notes)
    times = {p: metadata.suggest_time(p).strftime("%Y-%m-%dT%H:%M") for p in store.PLATFORMS}
    return {"source": _summary(src), "meta": meta, "suggested_times": times}


@router.get("/sources")
def list_sources(limit: int = 40):
    """Videos listos para publicar: clips exportados y videos finales del Studio, los más recientes primero."""
    with db.get_conn() as conn:
        clips = conn.execute("""SELECT r.id, r.path, r.created_at, r.duration, c.title, c.video_id AS parent_id
                                FROM renders r JOIN clips c ON c.id = r.clip_id ORDER BY r.id DESC LIMIT ?""", (limit,)).fetchall()
        finals = conn.execute("""SELECT r.id, r.path, r.created_at, r.duration, p.name AS title, p.id AS parent_id
                                 FROM studio_renders r JOIN studio_projects p ON p.id = r.project_id WHERE r.quality = 'final'
                                 ORDER BY r.id DESC LIMIT ?""", (limit,)).fetchall()
    out = [{"kind": "clip_render", "id": r["id"], "title": r["title"], "created_at": r["created_at"],
            "duration": r["duration"], "url": f"/api/renders/{r['id']}/file",
            "parent_kind": "video", "parent_id": r["parent_id"]} for r in clips if Path(r["path"]).exists()]
    out += [{"kind": "studio_render", "id": r["id"], "title": r["title"], "created_at": r["created_at"],
             "duration": r["duration"], "url": f"/api/studio/renders/{r['id']}/file",
             "parent_kind": "project", "parent_id": r["parent_id"]} for r in finals if Path(r["path"]).exists()]
    return sorted(out, key=lambda x: x["created_at"] or "", reverse=True)[:limit]


@router.get("/source")
def get_source(kind: Literal["clip_render", "studio_render"], id: int):
    return _summary(_source(kind, id))


def _when(s: str | None) -> str | None:
    if not s:
        return None
    try:
        t = datetime.fromisoformat(s.replace("Z", ""))
    except ValueError as exc:
        raise HTTPException(400, "Fecha de programación no válida.") from exc
    return t.strftime("%Y-%m-%d %H:%M:%S")


class Target(BaseModel):
    platform: Platform
    account_id: int
    meta: dict[str, Any]
    options: dict[str, Any] = {}
    scheduled_at: str | None = None


class PostReq(BaseModel):
    source_kind: Literal["clip_render", "studio_render"]
    source_id: int
    targets: list[Target]
    scheduled_at: str | None = None  # None = publicar ahora


@router.post("/posts")
def create_posts(req: PostReq):
    if not req.targets:
        raise HTTPException(400, "Elige al menos una red.")
    src = _source(req.source_kind, req.source_id)
    batch = secrets.token_hex(6)
    ids = []
    for t in req.targets:
        acc = store.get_account(t.account_id)
        if not acc or acc["platform"] != t.platform:
            raise HTTPException(400, f"Conecta una cuenta de {store.PLATFORM_NAMES[t.platform]} antes de publicar.")
    for t in req.targets:
        when = _when(t.scheduled_at or req.scheduled_at)
        opts = {**t.options, "vertical": src["vertical"]}
        ids.append(store.create_post(
            batch=batch, platform=t.platform, account_id=t.account_id, source_kind=src["kind"], source_id=src["id"],
            file_path=src["path"], title=(t.meta.get("title") or src["title"] or "")[:200], meta=t.meta, options=opts,
            status="scheduled" if when else "queued", scheduled_at=when))
    runner.wake()
    return {"batch": batch, "ids": ids, "posts": [_post_out(store.get_post(i)) for i in ids]}


def _post_out(p: dict) -> dict:
    acc = store.get_account(p["account_id"]) if p.get("account_id") else None
    return {**p, "account": store.account_public(acc) if acc else None,
            "file_exists": Path(p["file_path"]).exists()}


@router.get("/posts")
def list_posts(status: str | None = None):
    return [_post_out(p) for p in store.list_posts(status)]


def _get(pid: int) -> dict:
    p = store.get_post(pid)
    if not p:
        raise HTTPException(404, "Publicación no encontrada.")
    return p


class PostPatch(BaseModel):
    meta: dict[str, Any] | None = None
    options: dict[str, Any] | None = None
    scheduled_at: str | None = None


@router.patch("/posts/{pid}")
def edit_post(pid: int, req: PostPatch):
    p = _get(pid)
    if p["status"] not in ("scheduled", "failed", "cancelled"):
        raise HTTPException(400, "Solo se pueden editar publicaciones programadas o con error.")
    f: dict = {}
    if req.meta is not None:
        f["meta"] = {**p["meta"], **req.meta}
        if req.meta.get("title"):
            f["title"] = req.meta["title"][:200]
    if req.options is not None:
        f["options"] = {**p["options"], **req.options}
    if req.scheduled_at is not None:
        f["scheduled_at"] = _when(req.scheduled_at)
        if p["status"] == "scheduled" and not f["scheduled_at"]:
            raise HTTPException(400, "Indica la nueva fecha.")
    if f:
        store.update_post(pid, **f)
    return _post_out(store.get_post(pid))


@router.post("/posts/{pid}/now")
def publish_now(pid: int):
    p = _get(pid)
    if p["status"] not in ("scheduled", "failed", "cancelled"):
        raise HTTPException(400, "Esta publicación ya está en proceso o publicada.")
    store.update_post(pid, status="queued", error=None, stage="En cola", progress=0)
    runner.wake()
    return _post_out(store.get_post(pid))


@router.post("/posts/{pid}/retry")
def retry(pid: int):
    return publish_now(pid)


@router.post("/posts/{pid}/cancel")
def cancel(pid: int):
    p = _get(pid)
    if p["status"] not in ("scheduled", "queued"):
        raise HTTPException(400, "Solo se cancelan publicaciones pendientes.")
    store.update_post(pid, status="cancelled", stage="Cancelado")
    return _post_out(store.get_post(pid))


@router.delete("/posts/{pid}")
def delete_post(pid: int):
    p = _get(pid)
    if p["status"] == "publishing":
        raise HTTPException(400, "Espera a que termine la subida.")
    with db.get_conn() as conn:
        conn.execute("DELETE FROM publish_posts WHERE id = ?", (pid,))
    return {"ok": True}
