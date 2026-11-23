"""Busca recursos REALES (imágenes, logos, capturas, clips cortos) para lo que se dice, los verifica con visión y
los guarda en el índice para reutilizarlos. Si no hay nada verificable, no devuelve nada (no se inventa).

Orden: índice local (reutilizar, mejor si es una variante aún no usada en esta edición) → internet → repetir uno local.
"""
from __future__ import annotations

import base64
import hashlib
import io
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutTimeout
from pathlib import Path
from urllib.parse import urlparse

from .. import db, research
from ..ai import llm
from . import store
from .planner import plan_needs

STOCK = ("shutterstock", "alamy", "gettyimages", "istockphoto", "dreamstime", "depositphotos", "123rf",
         "adobestock", "stock.adobe", "pond5", "vectorstock", "freepik", "pngtree", "colourbox")
BAD_WORDS = ("meme", "memes", "parody", "parodia", "cartoon", "caricatura", "reaction", "reaccion", "reacción",
             "fan art", "fanart", "animation meme", "edit audio")
FIT = {"logo": "contain", "captura_web": "contain", "producto": "contain", "noticia": "contain",
       "persona": "cover", "lugar": "cover", "clip": "cover", "juego": "cover"}
BRAND = {"xbox": "#107C10", "playstation": "#003791", "nintendo": "#E60012", "reddit": "#FF4500",
         "youtube": "#FF0000", "twitch": "#9146FF", "nvidia": "#76B900", "amd": "#ED1C24", "steam": "#1B2838",
         "discord": "#5865F2", "facebook": "#1877F2", "instagram": "#E1306C", "tiktok": "#010101"}
TEXT_COLORS = ("#E63946", "#FFB703", "#2A9D8F", "#7B2CBF", "#1D4ED8", "#F97316", "#10B981", "#DB2777")
MAX_VERIFY = 2
CLIP_S = 6.0

VERIFY_SCHEMA = {
    "type": "object",
    "properties": {"score": {"type": "number"}, "shows_entity": {"type": "boolean"},
                   "watermark": {"type": "boolean"}, "meme": {"type": "boolean"}, "reason": {"type": "string"}},
    "required": ["score", "shows_entity"],
}

_vision_cache: dict = {"t": 0.0, "ok": False, "model": None}


# ---------------- utilidades ----------------

def _vision() -> tuple[str, str] | None:
    """(url, modelo) si el modelo de visión está disponible (se revisa cada 60 s)."""
    url = db.get_setting("ollama_url")
    model = db.get_setting("vision_model") or "qwen3.6:35b-a3b"
    if time.time() - _vision_cache["t"] > 60 or _vision_cache["model"] != model:
        try:
            st = llm.status(url)
            ok = bool(st["available"] and model in st["models"])
        except Exception:  # noqa: BLE001
            ok = False
        _vision_cache.update(t=time.time(), ok=ok, model=model)
    return (url, model) if _vision_cache["ok"] else None


def _b64(img, max_side: int = 768) -> str:
    from PIL import Image
    im = img.copy()
    if im.mode in ("RGBA", "LA", "P"):
        im = im.convert("RGBA")
        bg = Image.new("RGBA", im.size, (128, 128, 128, 255))  # gris: se ven logos blancos y negros
        bg.alpha_composite(im)
        im = bg
    im = im.convert("RGB")
    im.thumbnail((max_side, max_side))
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=85)
    return base64.b64encode(buf.getvalue()).decode()


def _domain(url: str) -> str:
    try:
        return urlparse(url or "").netloc.lower().removeprefix("www.")
    except ValueError:
        return ""


def _has_alpha(im) -> bool:
    if im.mode == "P" and "transparency" in im.info:
        im = im.convert("RGBA")
    if im.mode not in ("RGBA", "LA"):
        return False
    return im.getchannel("A").getextrema()[0] < 250


def _hex(rgb) -> str:
    return "#%02X%02X%02X" % tuple(int(x) for x in rgb[:3])


def _lum(rgb) -> float:
    return 0.2126 * rgb[0] + 0.7152 * rgb[1] + 0.0722 * rgb[2]


def logo_bg(im, entity: str) -> str:
    """Color de fondo para mostrar el logo al centro: el color de la marca (el más presente y saturado del logo,
    o uno conocido); si el logo es casi todo de ese color, un fondo neutro que contraste."""
    rgba = im.convert("RGBA")
    rgba.thumbnail((96, 96))
    px = [p for p in rgba.getdata() if p[3] > 200]
    known = next((c for k, c in BRAND.items() if k in store.norm(entity).split()), None)
    if not px:
        return known or "#111318"
    buckets: dict = {}
    for r, g, b, _ in px:
        mx, mn = max(r, g, b), min(r, g, b)
        if mx - mn < 40 or mx < 40 or mn > 225:  # grises, negros y blancos no son "la marca"
            continue
        k = (r // 32, g // 32, b // 32)
        buckets.setdefault(k, []).append((r, g, b))
    if not buckets:
        brand = None
    else:
        best = max(buckets.values(), key=len)
        brand = tuple(sum(c[i] for c in best) // len(best) for i in range(3))
    if brand is None:
        brand_hex = known
        mostly = False
    else:
        brand_hex = _hex(brand)
        mostly = len(max(buckets.values(), key=len)) / len(px) > 0.5
    if not brand_hex:
        avg = sum(_lum(p) for p in px) / len(px)
        return "#111318" if avg > 128 else "#F4F4F5"
    if mostly:  # el logo es de ese color: sobre ese mismo color no se vería
        rgb = tuple(int(brand_hex[i:i + 2], 16) for i in (1, 3, 5))
        return "#F4F4F5" if _lum(rgb) < 110 else "#111318"
    return brand_hex


def _save_image(src: Path, what: str) -> tuple[Path, int, int, object] | None:
    from PIL import Image
    try:
        im = Image.open(src)
        im.load()
    except Exception:  # noqa: BLE001
        return None
    alpha = _has_alpha(im)
    im = im.convert("RGBA" if alpha else "RGB")
    im.thumbnail((1920, 1920))
    buf = io.BytesIO()
    if alpha:
        im.save(buf, "PNG", optimize=True)
        ext = "png"
    else:
        im.save(buf, "JPEG", quality=92)
        ext = "jpg"
    data = buf.getvalue()
    store.WEB_DIR.mkdir(parents=True, exist_ok=True)
    out = store.WEB_DIR / f"{hashlib.sha1(data).hexdigest()[:20]}.{ext}"
    if not out.exists():
        out.write_bytes(data)
    return out, im.width, im.height, im


def _register(path: Path, media_type: str, need: dict, src_url: str, page_url: str, title: str, w, h,
              dur, score, bg: str | None) -> dict:
    from ..studio import assets
    ent = need["entity"]
    tags = " ".join(dict.fromkeys(store.tokens(ent) + store.tokens(need.get("query") or "")[:6] + [need["what"], "web"]))
    aid = assets._upsert(path, "broll", media_type, ent[:120], tags, dur)
    note = f"Fuente: {_domain(page_url or src_url)} — revisa los derechos de uso antes de publicar"
    rid = store.add(aid, ent, need["what"], need.get("query") or "", src_url, page_url, title, w, h, dur, score,
                    note, bg)
    store.touch(rid)
    return {"index_id": rid, "asset_id": aid, "path": str(path), "media_type": media_type,
            "kind": "media", "entity": ent, "what": need["what"],
            "width": w, "height": h, "duration": dur, "source_url": src_url, "page_url": page_url, "score": score,
            "reused": False, "fit": FIT.get(need["what"], "cover"), "bg_color": bg}


def _from_index(r: dict, need: dict) -> dict:
    store.touch(r["id"])
    return {"index_id": r["id"], "asset_id": r["asset_id"], "path": r["path"], "media_type": r["media_type"],
            "kind": "media", "entity": r["entity"], "what": r["what"],
            "width": r.get("width"), "height": r.get("height"), "duration": r.get("duration"),
            "source_url": r.get("source_url"), "page_url": r.get("page_url"), "score": r.get("verified_score"),
            "reused": True, "fit": FIT.get(r["what"], "cover"), "bg_color": r.get("bg_color")}


def _verify(imgs: list, need: dict) -> dict | None:
    """Juez con visión. None si no hay modelo de visión."""
    v = _vision()
    if not v:
        return None
    url, model = v
    what = need["what"].replace("_", " ")
    prompt = (f"¿Esta imagen muestra claramente «{need['entity']}» (tipo: {what})? El creador dice: "
              f"«{(need.get('said') or '')[:200]}». Califica de 0 a 10 qué tan bien sirve para mostrarlo en un video "
              "de YouTube: 0-3 no es eso o es otra cosa; 4-5 dudoso o mala calidad; 6-8 sí es y se ve bien; 9-10 "
              "perfecto. Baja la nota si es un meme, una caricatura, si tiene marca de agua grande, si es muy "
              "pequeña o borrosa. Responde solo JSON.")
    try:
        out = llm.chat_json(url, model, "Eres un verificador de imágenes para edición de video. Responde solo JSON.",
                            prompt, VERIFY_SCHEMA, images=[_b64(i) for i in imgs], num_ctx=4096,
                            keep_alive="5m", timeout=240, max_tokens=160)
    except Exception:  # noqa: BLE001
        return {"score": 0.0, "shows_entity": False, "reason": "el verificador falló"}
    try:
        out["score"] = float(out.get("score") or 0)
    except (TypeError, ValueError):
        out["score"] = 0.0
    return out


def _accept(ver: dict | None, need: dict, title: str, w: int, h: int) -> tuple[bool, float | None]:
    if ver is not None:
        ok = ver["score"] >= 6 and bool(ver.get("shows_entity")) and not ver.get("meme") and not ver.get("watermark")
        return ok, round(ver["score"], 1)
    # sin visión: solo si el título/la dirección nombran la cosa y la imagen es grande
    big = max(w, h) >= (300 if need["what"] == "logo" else 400)
    return bool(big and store.mentions(need["entity"], title)), None


# ---------------- imágenes ----------------

def _queries(need: dict) -> list[str]:
    e, q, what = need["entity"], (need.get("query") or "").strip(), need["what"]
    extra = {"logo": [f"{e} logo png transparent", f"{e} logo"],
             "captura_web": [f"{e} página web", f"{e} website screenshot"],
             "producto": [e], "noticia": [f"{e} noticia"], "persona": [e], "lugar": [e],
             "clip": [f"{e} trailer"], "juego": [f"{e} gameplay screenshot", f"{e} screenshot"]}.get(what, [e])
    out = []
    for x in ([q] if q else []) + extra:
        if x and x.lower() not in (y.lower() for y in out):
            out.append(x)
    return out[:3]


def _rank(c: dict, need: dict) -> float:
    title = f"{c.get('title', '')} {c.get('url', '')} {c.get('image', '')}"
    dom = _domain(c.get("url") or c.get("image"))
    main = max(store.tokens(need["entity"]) or [""], key=len)
    s = 0.0
    if store.mentions(need["entity"], title):
        s += 3
    if main and main in dom.replace(".", " ").replace("-", " ").split() or (main and main in dom):
        s += 3  # sitio oficial
    if "wikipedia" in dom or "wikimedia" in dom:
        s += 2
    if need["what"] == "logo" and str(c.get("image", "")).lower().split("?")[0].endswith(".png"):
        s += 2
    w, h = c.get("width") or 0, c.get("height") or 0
    s += min(2.0, max(w, h) / 1000)
    return s


def _candidates(need: dict) -> list[dict]:
    seen, out = set(), []
    for q in _queries(need):
        try:
            res = research.images(q, 10)
        except research.ResearchError:
            continue
        for c in res:
            img = c.get("image") or ""
            low = f"{img} {c.get('url', '')} {c.get('title', '')}".lower()
            if not img or img in seen or any(s in low for s in STOCK):
                continue
            if any(b in low for b in BAD_WORDS) and not any(b in need["entity"].lower() for b in BAD_WORDS):
                continue
            w, h = c.get("width") or 0, c.get("height") or 0
            min_side = 150 if need["what"] == "logo" else 300
            if (w and h) and (min(w, h) < min_side or max(w, h) / max(min(w, h), 1) > 3.2):
                continue
            seen.add(img)
            out.append(c)
        if len(out) >= 12:
            break
    out.sort(key=lambda c: -_rank(c, need))
    return out


def _web_image(need: dict, use_vision: bool = True) -> dict | None:
    tmp = store.WEB_DIR / "tmp"
    verified = 0
    for c in _candidates(need)[:6]:
        old = store.by_source(c["image"])  # misma dirección ya descargada → reutilizar sin bajarla otra vez
        if old and Path(old["path"]).exists():
            if old.get("verified_score") is None or old["verified_score"] >= 6:
                return _from_index(old, need)
            continue
        p = research.download(c["image"], tmp / f"{hashlib.sha1(c['image'].encode()).hexdigest()[:16]}.img")
        if not p:
            continue
        saved = _save_image(p, need["what"])
        p.unlink(missing_ok=True)
        if not saved:
            continue
        path, w, h, im = saved
        if max(w, h) < (150 if need["what"] == "logo" else 300):
            continue
        ver = _verify([im], need) if use_vision else None
        if ver is not None:
            verified += 1
        ok, score = _accept(ver, need, f"{c.get('title', '')} {c.get('url', '')}", w, h)
        if ok:
            bg = logo_bg(im, need["entity"]) if need["what"] == "logo" else None
            return _register(path, "image", need, c["image"], c.get("url") or "", c.get("title") or "", w, h, None,
                             score, bg)
        if verified >= MAX_VERIFY:
            break
    return None


# ---------------- clips cortos (trailers / gameplay) ----------------

def _pick_video(need: dict) -> list[dict]:
    e = need["entity"]
    q = need.get("query") or e
    q = q if any(w in q.lower() for w in ("trailer", "gameplay", "tráiler")) else \
        f"{q} {'gameplay' if need['what'] == 'juego' else 'trailer oficial'}"
    try:
        res = research.videos(q, 8)
    except research.ResearchError:
        return []
    out = []
    for v in res:
        low = f"{v.get('title', '')}".lower()
        if any(b in low for b in BAD_WORDS) or not store.mentions(e, v.get("title", "")):
            continue
        if not (20 <= (v.get("duration") or 0) <= 5400):
            continue
        who = store.norm(f"{v.get('uploader', '')} {v.get('publisher', '')}")
        main = max(store.tokens(e) or [""], key=len)
        v["_r"] = (2 if main and main in who else 0) + (1 if any(w in low for w in ("official", "oficial")) else 0) \
            + min(2.0, (v.get("views") or 0) / 2_000_000)
        out.append(v)
    out.sort(key=lambda v: -v["_r"])
    return out[:2]


def _dl_section(url: str, start: float, end: float, out_dir: Path) -> Path | None:
    try:
        import yt_dlp
        from yt_dlp.utils import download_range_func
    except ImportError:
        return None
    out_dir.mkdir(parents=True, exist_ok=True)
    opts = {"format": "bv*[height<=720][ext=mp4]/bv*[height<=720]/b[height<=720]", "quiet": True,
            "no_warnings": True, "noplaylist": True, "socket_timeout": 20,
            "outtmpl": str(out_dir / "%(id)s_%(section_start)s.%(ext)s"),
            "download_ranges": download_range_func(None, [(start, end)]), "force_keyframes_at_cuts": False}
    try:
        from ..studio import refs
        opts.update(refs.cookie_source() or {})
    except Exception:  # noqa: BLE001
        pass

    def run():
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.download([url])

    ex = ThreadPoolExecutor(max_workers=1)
    try:
        ex.submit(run).result(timeout=150)
    except (FutTimeout, Exception):  # noqa: BLE001
        return None
    finally:
        ex.shutdown(wait=False)
    files = sorted((p for p in out_dir.glob("*") if p.suffix in (".mp4", ".webm", ".mkv") and p.stat().st_size > 10_000),
                   key=lambda p: p.stat().st_mtime, reverse=True)
    return files[0] if files else None


def _frames(path: Path, dur: float) -> list:
    from PIL import Image
    out = []
    for f in (0.3, 0.75):
        try:
            raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{dur * f:.2f}", "-i", str(path), "-frames:v", "1",
                                  "-f", "image2pipe", "-vcodec", "mjpeg", "-"], capture_output=True, timeout=60).stdout
            if raw:
                out.append(Image.open(io.BytesIO(raw)).convert("RGB"))
        except (OSError, subprocess.TimeoutExpired, Exception):  # noqa: BLE001
            continue
    return out


def _web_clip(need: dict, use_vision: bool = True) -> dict | None:
    from ..studio.probe import ProbeError, probe_any
    for v in _pick_video(need):
        d = float(v["duration"])
        start = max(3.0, min(d * (0.3 if need["what"] == "juego" else 0.2), d - CLIP_S - 2))
        src = f"{v['url']}&t={int(start)}"
        old = store.by_source(src)
        if old and Path(old["path"]).exists():
            return _from_index(old, need)
        tmp = store.WEB_DIR / "tmp" / hashlib.sha1(src.encode()).hexdigest()[:12]
        p = _dl_section(v["url"], start, start + CLIP_S, tmp)
        if not p:
            continue
        try:
            meta = probe_any(p)
        except ProbeError:
            continue
        dur = float(meta.get("duration") or 0)
        if dur < 2:
            continue
        frames = _frames(p, dur)
        if not frames:
            continue
        ver = _verify(frames, need) if use_vision else None
        ok, score = _accept(ver, need, v.get("title", ""), meta.get("width") or 1280, meta.get("height") or 720)
        if not ok:
            continue
        store.WEB_DIR.mkdir(parents=True, exist_ok=True)
        out = store.WEB_DIR / f"{hashlib.sha1(src.encode()).hexdigest()[:20]}{p.suffix}"
        p.replace(out)
        for x in tmp.glob("*"):
            x.unlink(missing_ok=True)
        try:
            tmp.rmdir()
        except OSError:
            pass
        return _register(out, "video", need, src, v["url"], v.get("title", ""), meta.get("width"), meta.get("height"),
                         round(dur, 2), score, None)
    return None


# ---------------- API ----------------

def _group_ok(r: dict, what: str) -> bool:
    if what in store.VISUAL_VIDEO:
        return True  # para un clip sirve también una imagen del juego si no hay video
    return r["media_type"] == "image"


def find(need: dict, allow_web: bool = True, verify: bool = True, exclude: set | None = None) -> dict | None:
    """Recurso real para una necesidad. `exclude`: assets ya usados en esta edición (se prefieren variantes)."""
    what = need.get("what")
    if what == "texto_clave":
        h = int(hashlib.sha1(store.norm(need["entity"]).encode()).hexdigest(), 16)
        bg = TEXT_COLORS[h % len(TEXT_COLORS)]
        rgb = tuple(int(bg[i:i + 2], 16) for i in (1, 3, 5))
        return {"kind": "text", "text": need["entity"], "sub": need.get("label") or "",
                "color": "#111318" if _lum(rgb) > 150 else "#FFFFFF", "bg_color": bg, "asset_id": None,
                "media_type": None, "entity": need["entity"], "what": what, "reused": False, "fit": None,
                "path": None, "width": None, "height": None, "duration": None, "source_url": None,
                "page_url": None, "score": None}
    exclude = exclude or set()
    local = [r for r in store.index_search(need["entity"], limit=12)
             if _group_ok(r, what) and store.mentions(need["entity"], r["entity"])
             and (r.get("verified_score") is None or r["verified_score"] >= 6)]
    if what in store.VISUAL_VIDEO:
        local.sort(key=lambda r: 0 if r["media_type"] == "video" else 1)
    else:
        local.sort(key=lambda r: 0 if r["what"] == what else 1)
    fresh = [r for r in local if r["asset_id"] not in exclude and (r["what"] == what or what in store.VISUAL_VIDEO)]
    if fresh:
        return _from_index(fresh[0], need)
    if allow_web and research.enabled():
        kw = {} if verify else {"use_vision": False}
        res = None
        if what in store.VISUAL_VIDEO:
            res = _web_clip(need, **kw)
        if res is None:
            res = _web_image(need, **kw)
        if res:
            return res
    if local:
        return _from_index(local[0], need)  # mejor repetir algo real que no poner nada
    return None


def gather(tl: dict, understood: dict | None, research_facts: dict | None, per_min: float, progress=None,
           mode: str = "cards", allow_web: bool = True, time_cap: float = 360.0, max_items: int = 60) -> list[dict]:
    """Plan + búsqueda. Devuelve [{need, resource}] de TODAS las necesidades, en orden de tiempo;
    resource = None cuando no se encontró nada verificable. Sin internet usa el índice local y las tarjetas de texto."""
    prog = progress or (lambda p, s: None)
    needs = plan_needs(tl, understood, research_facts, per_min, max_items=max_items, mode=mode,
                       progress=lambda p, s: prog(0.3 * p, s))
    if mode != "background":  # tarjetas: la misma cosa no se repite en menos de 30 s
        kept, last = [], {}
        for n in needs:
            k = store.norm(n["entity"])
            if k in last and n["t0"] - last[k] < 30:
                continue
            last[k] = n["t0"]
            kept.append(n)
        needs = kept
    out, used = [], set()
    t_start = time.time()
    for i, n in enumerate(needs):
        web = allow_web and time.time() - t_start < time_cap
        prog(0.3 + 0.7 * i / max(1, len(needs)), f"Buscando «{n['entity'][:30]}» ({i + 1}/{len(needs)})")
        try:
            r = find(n, allow_web=web, exclude=used)
        except Exception:  # noqa: BLE001 — un recurso que falla no tumba la edición
            r = None
        if r and r.get("asset_id"):
            used.add(r["asset_id"])
        out.append({"need": n, "resource": r})
    out.sort(key=lambda x: x["need"]["t0"])
    prog(1.0, f"{sum(1 for x in out if x['resource'])} de {len(needs)} recursos listos")
    return out
