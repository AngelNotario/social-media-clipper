"""Subida a cada red con su API oficial.

YouTube: subida reanudable (videos.insert). Los proyectos de Google sin auditar quedan en privado.
TikTok: "Borrador en bandeja" (inbox, terminas en la app con el audio en tendencia) o publicación directa
        (privada SELF_ONLY hasta que TikTok audite tu app).
Instagram: Reel con subida reanudable desde el archivo local (rupload.facebook.com) y publicación.
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Callable

import httpx

from . import oauth

Progress = Callable[[float, str], None]


class PublishError(Exception):
    pass


def http() -> httpx.Client:
    return httpx.Client(timeout=httpx.Timeout(60, read=600, write=600), follow_redirects=True)


def _err(r: httpx.Response, what: str) -> PublishError:
    try:
        d = r.json()
    except ValueError:
        d = {}
    e = d.get("error") if isinstance(d, dict) else None
    if isinstance(e, dict):
        msg = e.get("message") or e.get("code") or ""
        reasons = [x.get("reason") for x in e.get("errors") or [] if isinstance(x, dict)]
        if "uploadLimitExceeded" in reasons:
            msg = "YouTube: alcanzaste el límite de subidas del canal por hoy."
        elif "quotaExceeded" in reasons:
            msg = "YouTube: se agotó la cuota diaria de tu proyecto de Google."
    else:
        msg = (d.get("error_description") if isinstance(d, dict) else None) or e or r.text[:300]
    return PublishError(f"{what} ({r.status_code}): {msg}")


# ---------------------------------------------------------------- YouTube

YT_CATEGORIES = {"gaming": "20", "entretenimiento": "24", "gente": "22", "comedia": "23", "educacion": "27",
                 "ciencia": "28", "noticias": "25", "howto": "26", "deportes": "17", "musica": "10"}


def youtube_upload(account: dict, path: Path, meta: dict, options: dict, progress: Progress) -> dict:
    token = oauth.access_token(account)
    tags, total = [], 0
    for t in meta.get("tags") or []:  # YouTube admite 500 caracteres de etiquetas en total
        t = str(t).strip().lstrip("#")
        if t and total + len(t) + 1 <= 480:
            tags.append(t)
            total += len(t) + 1
    status = {"privacyStatus": options.get("privacy") or "public", "selfDeclaredMadeForKids": False,
              "embeddable": True}
    if options.get("publish_at"):  # programado nativo en YouTube (requiere privado)
        status.update(privacyStatus="private", publishAt=options["publish_at"])
    body = {"snippet": {"title": (meta.get("title") or path.stem)[:100], "description": (meta.get("description") or "")[:4900],
                        "tags": tags, "categoryId": str(meta.get("category_id") or "24"),
                        "defaultLanguage": meta.get("language") or "es", "defaultAudioLanguage": meta.get("language") or "es"},
            "status": status}
    size = path.stat().st_size
    with http() as cl:
        progress(0.05, "YouTube: iniciando subida")
        r = cl.post("https://www.googleapis.com/upload/youtube/v3/videos",
                    params={"uploadType": "resumable", "part": "snippet,status"}, json=body,
                    headers={"Authorization": f"Bearer {token}", "X-Upload-Content-Type": "video/mp4",
                             "X-Upload-Content-Length": str(size)})
        if r.status_code >= 400 or "location" not in r.headers:
            raise _err(r, "YouTube no aceptó la subida")
        loc = r.headers["location"]
        chunk = 8 * 1024 * 1024
        res = None
        with open(path, "rb") as f:
            sent = 0
            while sent < size:
                data = f.read(chunk)
                end = sent + len(data) - 1
                r = cl.put(loc, content=data, headers={"Authorization": f"Bearer {token}", "Content-Type": "video/mp4",
                                                       "Content-Range": f"bytes {sent}-{end}/{size}"})
                sent = end + 1
                progress(0.05 + 0.9 * sent / size, f"YouTube: subiendo {int(100 * sent / size)}%")
                if r.status_code in (200, 201):
                    res = r.json()
                    break
                if r.status_code != 308:
                    raise _err(r, "YouTube cortó la subida")
        if not res:
            raise PublishError("YouTube no confirmó la subida.")
    vid = res["id"]
    vertical = bool(options.get("vertical"))
    sn = res.get("snippet") or {}
    sent_title = body["snippet"]["title"]
    got = (res.get("status") or {}).get("privacyStatus")
    out = {"remote_id": vid, "remote_url": f"https://youtube.com/shorts/{vid}" if vertical else f"https://youtu.be/{vid}",
           "privacy": got, "sent": {"title": sent_title, "description": body["snippet"]["description"], "tags": tags},
           "youtube_saw": {"title": sn.get("title"), "description": sn.get("description")}}
    if sn.get("title") and sn["title"].strip() != sent_title.strip():
        out["note"] = (out.get("note", "") + f" YouTube guardó el título como «{sn['title']}» (enviado: «{sent_title}»).").strip()
    th = options.get("thumbnail_id")
    if th:  # miniatura personalizada (YouTube exige canal verificado por teléfono)
        from ..thumbs import store as tstore
        t = tstore.get(int(th))
        if t and Path(t["path"]).exists():
            with http() as cl:
                r = cl.post("https://www.googleapis.com/upload/youtube/v3/thumbnails/set", params={"videoId": vid},
                            content=Path(t["path"]).read_bytes(),
                            headers={"Authorization": f"Bearer {token}", "Content-Type": "image/jpeg"})
            if r.status_code < 300:
                out["thumbnail"] = "ok"
            else:
                out["thumbnail"] = "error"
                msg = str(_err(r, "miniatura"))
                out["note"] = (out.get("note", "") + " La miniatura no se pudo poner: " +
                               ("verifica tu canal por teléfono en youtube.com/verify para usar miniaturas personalizadas."
                                if r.status_code == 403 else msg)).strip()
    if got == "private" and status["privacyStatus"] != "private":
        out["note"] = (out.get("note", "") + " " if out.get("note") else "") + ("YouTube lo dejó en privado: tu proyecto de Google aún no está auditado. Puedes pedir la auditoría "
                       "(gratis) o hacerlo público manualmente si YouTube Studio te lo permite.")
    return out


# ---------------------------------------------------------------- TikTok

TT = "https://open.tiktokapis.com/v2/post/publish"


def _tt_chunks(size: int) -> tuple[int, int]:
    """TikTok: trozos de 5-64 MB; un archivo de hasta 64 MB va en un solo trozo; el último absorbe el resto."""
    if size <= 64 * 1024 * 1024:
        return size, 1
    chunk = 10 * 1024 * 1024
    return chunk, size // chunk


def tiktok_creator_info(account: dict) -> dict:
    token = oauth.access_token(account)
    with http() as cl:
        r = cl.post(f"{TT}/creator_info/query/", headers={"Authorization": f"Bearer {token}",
                                                         "Content-Type": "application/json; charset=UTF-8"})
    if r.status_code >= 400:
        raise _err(r, "TikTok no devolvió la información del creador")
    return r.json().get("data") or {}


def tiktok_upload(account: dict, path: Path, meta: dict, options: dict, progress: Progress) -> dict:
    token = oauth.access_token(account)
    size = path.stat().st_size
    chunk, count = _tt_chunks(size)
    source = {"source": "FILE_UPLOAD", "video_size": size, "chunk_size": chunk, "total_chunk_count": count}
    mode = options.get("mode") or "inbox"
    hdr = {"Authorization": f"Bearer {token}", "Content-Type": "application/json; charset=UTF-8"}
    with http() as cl:
        progress(0.05, "TikTok: iniciando")
        if mode == "direct":
            info = tiktok_creator_info(account)
            levels = info.get("privacy_level_options") or ["SELF_ONLY"]
            want = options.get("privacy") or "PUBLIC_TO_EVERYONE"
            post_info = {"title": (meta.get("caption") or "")[:2200],
                         "privacy_level": want if want in levels else ("SELF_ONLY" if "SELF_ONLY" in levels else levels[0]),
                         "disable_comment": bool(options.get("disable_comment")),
                         "disable_duet": bool(options.get("disable_duet")),
                         "disable_stitch": bool(options.get("disable_stitch")),
                         "video_cover_timestamp_ms": int(options.get("cover_ms") or 1000),
                         "is_aigc": bool(options.get("is_aigc"))}
            r = cl.post(f"{TT}/video/init/", json={"post_info": post_info, "source_info": source}, headers=hdr)
        else:
            r = cl.post(f"{TT}/inbox/video/init/", json={"source_info": source}, headers=hdr)
        d = r.json() if r.content else {}
        if r.status_code >= 400 or (d.get("error") or {}).get("code", "ok") != "ok":
            e = d.get("error") or {}
            if e.get("code") == "unaudited_client_can_only_post_to_private_accounts":
                raise PublishError("TikTok: tu app no está auditada; para publicar directo tu cuenta debe ser privada. "
                                   "Usa «Borrador en bandeja» o pide la auditoría.")
            raise PublishError(f"TikTok no aceptó la subida: {e.get('message') or e.get('code') or r.text[:300]}")
        data = d["data"]
        upload_url, publish_id = data["upload_url"], data["publish_id"]
        with open(path, "rb") as f:
            for i in range(count):
                start = i * chunk
                n = (size - start) if i == count - 1 else chunk
                body = f.read(n)
                r = cl.put(upload_url, content=body, headers={
                    "Content-Type": "video/mp4", "Content-Length": str(n),
                    "Content-Range": f"bytes {start}-{start + n - 1}/{size}"})
                if r.status_code not in (200, 201, 206):
                    raise _err(r, "TikTok cortó la subida")
                progress(0.05 + 0.75 * (i + 1) / count, f"TikTok: subiendo {int(100 * (i + 1) / count)}%")
        status = "PROCESSING_UPLOAD"
        for _ in range(60):  # hasta ~5 min procesando
            time.sleep(options.get("_poll_s", 5))
            r = cl.post(f"{TT}/status/fetch/", json={"publish_id": publish_id}, headers=hdr)
            sd = (r.json() if r.content else {}).get("data") or {}
            status = sd.get("status") or status
            progress(0.85, f"TikTok: {status.lower().replace('_', ' ')}")
            if status in ("SEND_TO_USER_INBOX", "PUBLISH_COMPLETE"):
                break
            if status == "FAILED":
                raise PublishError(f"TikTok falló al procesar el video: {sd.get('fail_reason') or 'sin detalle'}")
    out = {"remote_id": publish_id, "status": status}
    if status == "SEND_TO_USER_INBOX" or mode != "direct":
        out["note"] = ("Listo en tu bandeja de TikTok: abre la notificación en la app, elige el audio en tendencia, "
                       "pega el texto (ya está copiado en la publicación) y publica.")
    else:
        ids = sd.get("publicaly_available_post_id") or sd.get("publicly_available_post_id") or []
        if ids:
            out["post_id"] = str(ids[0])
    return out


# ---------------------------------------------------------------- Instagram

def instagram_upload(account: dict, path: Path, meta: dict, options: dict, progress: Progress) -> dict:
    token = oauth.access_token(account)
    ig = account["remote_id"]
    size = path.stat().st_size
    params = {"media_type": "REELS", "upload_type": "resumable", "caption": (meta.get("caption") or "")[:2200],
              "share_to_feed": "true" if options.get("share_to_feed", True) else "false", "access_token": token}
    if meta.get("audio_name"):
        params["audio_name"] = meta["audio_name"][:100]
    if options.get("cover_ms") is not None:
        params["thumb_offset"] = str(int(options["cover_ms"]))
    with http() as cl:
        progress(0.05, "Instagram: creando el Reel")
        r = cl.post(oauth.graph(f"{ig}/media"), data=params)
        if r.status_code >= 400:
            raise _err(r, "Instagram no aceptó el Reel")
        d = r.json()
        cid = d["id"]
        uri = d.get("uri") or f"https://rupload.facebook.com/ig-api-upload/{oauth.graph_version()}/{cid}"
        with open(path, "rb") as f:
            r = cl.post(uri, content=f.read(), headers={"Authorization": f"OAuth {token}", "offset": "0",
                                                        "file_size": str(size)})
        if r.status_code >= 400:
            raise _err(r, "Instagram cortó la subida")
        progress(0.6, "Instagram: procesando el video")
        code = "IN_PROGRESS"
        for _ in range(90):  # hasta ~7 min
            time.sleep(options.get("_poll_s", 5))
            r = cl.get(oauth.graph(cid), params={"fields": "status_code,status", "access_token": token})
            sd = r.json() if r.content else {}
            code = sd.get("status_code") or code
            if code in ("FINISHED", "PUBLISHED"):
                break
            if code in ("ERROR", "EXPIRED"):
                raise PublishError(f"Instagram no pudo procesar el video: {sd.get('status') or code}")
        if code not in ("FINISHED", "PUBLISHED"):
            raise PublishError("Instagram tardó demasiado procesando el video; reintenta más tarde.")
        progress(0.9, "Instagram: publicando")
        r = cl.post(oauth.graph(f"{ig}/media_publish"), data={"creation_id": cid, "access_token": token})
        if r.status_code >= 400:
            raise _err(r, "Instagram no publicó el Reel")
        mid = r.json()["id"]
        r = cl.get(oauth.graph(mid), params={"fields": "permalink", "access_token": token})
        link = (r.json() if r.content else {}).get("permalink")
    return {"remote_id": mid, "remote_url": link}


UPLOADERS = {"youtube": youtube_upload, "tiktok": tiktok_upload, "instagram": instagram_upload}

