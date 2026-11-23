"""Investigación en internet antes de escribir: para no desinformar ni usar recursos viejos.

- search / news / images / videos: buscador web sin clave (ddgs, con varios motores de respaldo), región México.
- read: descarga una página y saca su texto principal.
- fact_check: la IA compara lo que dice el creador con lo que dicen las fuentes de hoy y devuelve una ficha
  (confirmado / dudoso / contexto) con las fuentes, que usan los títulos, textos, metadatos y miniaturas.
- trending_videos: videos recientes y con más vistas sobre el tema (sus títulos y miniaturas = lo que funciona hoy).

Todo se guarda en caché unas horas para no repetir búsquedas.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from pathlib import Path

import httpx

from . import config, db

CACHE_DIR = config.DATA_DIR / "cache" / "research"
TTL = 6 * 3600
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/130.0 Safari/537.36")


class ResearchError(Exception):
    pass


def enabled() -> bool:
    import os
    if os.environ.get("SMC_NO_RESEARCH") == "1":  # pruebas sin red
        return False
    return db.get_setting("research_enabled") is not False


def region() -> str:
    return db.get_setting("research_region") or "mx-es"


def _cache(kind: str, key: str, fn, ttl: int = TTL):
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    f = CACHE_DIR / f"{kind}_{hashlib.sha1(key.encode()).hexdigest()[:16]}.json"
    if f.exists() and time.time() - f.stat().st_mtime < ttl:
        try:
            return json.loads(f.read_text(encoding="utf-8"))
        except ValueError:
            pass
    data = fn()
    try:
        f.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass
    return data


def _ddgs():
    try:
        from ddgs import DDGS
    except ImportError as exc:
        raise ResearchError("Falta el buscador (pip install ddgs).") from exc
    return DDGS(timeout=15)


def search(query: str, n: int = 6, timelimit: str | None = None) -> list[dict]:
    """→ [{title, url, snippet}]"""
    def run():
        try:
            res = _ddgs().text(query, region=region(), max_results=n, timelimit=timelimit, backend="auto")
        except Exception as exc:  # noqa: BLE001 — el buscador puede fallar o limitar
            raise ResearchError(f"La búsqueda falló: {exc}") from exc
        return [{"title": r.get("title", ""), "url": r.get("href", ""), "snippet": r.get("body", "")} for r in res]
    return _cache("s", f"{query}|{n}|{timelimit}|{region()}", run)


def news(query: str, n: int = 6, timelimit: str = "m") -> list[dict]:
    def run():
        try:
            res = _ddgs().news(query, region=region(), max_results=n, timelimit=timelimit)
        except Exception as exc:  # noqa: BLE001
            raise ResearchError(f"La búsqueda de noticias falló: {exc}") from exc
        return [{"title": r.get("title", ""), "url": r.get("url", ""), "snippet": r.get("body", ""),
                 "date": r.get("date", ""), "source": r.get("source", "")} for r in res]
    return _cache("n", f"{query}|{n}|{timelimit}|{region()}", run, ttl=3 * 3600)


def _int(v) -> int:
    """El buscador a veces da medidas como texto ("1280") o vacías."""
    try:
        return int(float(str(v).replace("px", "").strip()))
    except (TypeError, ValueError):
        return 0


def images(query: str, n: int = 8, license_image: str | None = None, layout: str | None = None) -> list[dict]:
    """→ [{title, image, thumbnail, url (página), width, height, source}]"""
    def run():
        kw = {"region": region(), "max_results": n}
        if license_image:
            kw["license_image"] = license_image
        if layout:
            kw["layout"] = layout
        try:
            res = _ddgs().images(query, **kw)
        except Exception as exc:  # noqa: BLE001
            raise ResearchError(f"La búsqueda de imágenes falló: {exc}") from exc
        return [{"title": r.get("title", ""), "image": r.get("image", ""), "thumbnail": r.get("thumbnail", ""),
                 "url": r.get("url", ""), "width": _int(r.get("width")), "height": _int(r.get("height")),
                 "source": r.get("source", "")} for r in res if r.get("image")]
    return _cache("i", f"{query}|{n}|{license_image}|{layout}|{region()}", run)


def youtube_id(url: str) -> str | None:
    m = re.search(r"(?:v=|youtu\.be/|shorts/|embed/)([A-Za-z0-9_-]{11})", url or "")
    return m.group(1) if m else None


def trending_videos(query: str, n: int = 8) -> list[dict]:
    """Videos del tema de este mes: título, vistas y miniatura (referencia de lo que está funcionando hoy)."""
    def run():
        try:
            res = _ddgs().videos(query, region=region(), max_results=n * 2, timelimit="m")
        except Exception as exc:  # noqa: BLE001
            raise ResearchError(f"La búsqueda de videos falló: {exc}") from exc
        out = []
        for r in res:
            url = r.get("content", "")
            vid = youtube_id(url)
            views = (r.get("statistics") or {}).get("viewCount") or 0
            thumb = f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg" if vid else ((r.get("images") or {}).get("large") or "")
            out.append({"title": r.get("title", ""), "url": url, "views": int(views or 0), "thumbnail": thumb,
                        "publisher": r.get("publisher", ""), "published": r.get("published", ""),
                        "uploader": r.get("uploader", "")})
        out.sort(key=lambda x: x["views"], reverse=True)
        return out[:n]
    return _cache("v", f"{query}|{n}|{region()}", run, ttl=3 * 3600)


def _dur_s(v) -> float:
    """"12:34" / "1:02:03" / 754 → segundos."""
    if isinstance(v, (int, float)):
        return float(v)
    try:
        parts = [float(x) for x in str(v or "").split(":") if x.strip()]
    except ValueError:
        return 0.0
    out = 0.0
    for x in parts:
        out = out * 60 + x
    return out


def videos(query: str, n: int = 8, timelimit: str | None = None) -> list[dict]:
    """Videos de YouTube sobre algo (trailers, gameplay): [{title, url, id, duration, views, publisher}]."""
    def run():
        kw = {"region": region(), "max_results": n * 2}
        if timelimit:
            kw["timelimit"] = timelimit
        try:
            res = _ddgs().videos(query, **kw)
        except Exception as exc:  # noqa: BLE001
            raise ResearchError(f"La búsqueda de videos falló: {exc}") from exc
        out = []
        for r in res:
            url = r.get("content", "")
            vid = youtube_id(url)
            if not vid:
                continue
            out.append({"title": r.get("title", ""), "url": f"https://www.youtube.com/watch?v={vid}", "id": vid,
                        "duration": _dur_s(r.get("duration")),
                        "views": int((r.get("statistics") or {}).get("viewCount") or 0),
                        "publisher": r.get("publisher", ""), "uploader": r.get("uploader", "")})
        return out[:n]
    return _cache("vv", f"{query}|{n}|{timelimit}|{region()}", run, ttl=6 * 3600)


def read(url: str, max_chars: int = 3500) -> str:
    """Texto principal de una página (sin menús ni anuncios)."""
    def run():
        try:
            r = httpx.get(url, headers={"User-Agent": UA, "Accept-Language": "es-MX,es;q=0.9"}, timeout=15,
                          follow_redirects=True)
            html = r.text if r.status_code < 400 else ""
        except httpx.HTTPError:
            html = ""
        text = ""
        if html:
            try:
                import trafilatura
                text = trafilatura.extract(html, include_comments=False, favor_recall=True) or ""
            except ImportError:
                text = re.sub(r"<[^>]+>", " ", re.sub(r"(?s)<(script|style).*?</\1>", " ", html))
        if not text:
            try:
                text = str(_ddgs().extract(url, fmt="text_plain").get("content") or "")
            except Exception:  # noqa: BLE001
                text = ""
        return re.sub(r"\s+", " ", text).strip()[:max_chars]
    return _cache("r", url, run, ttl=24 * 3600)


def download(url: str, dest: Path, max_bytes: int = 8_000_000) -> Path | None:
    """Descarga una imagen de internet (con límite de tamaño). None si no se pudo."""
    try:
        with httpx.stream("GET", url, headers={"User-Agent": UA, "Referer": url}, timeout=20, follow_redirects=True) as r:
            if r.status_code >= 400 or not r.headers.get("content-type", "").startswith("image"):
                return None
            data = b""
            for chunk in r.iter_bytes():
                data += chunk
                if len(data) > max_bytes:
                    return None
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        return dest
    except httpx.HTTPError:
        return None


FACT_SCHEMA = {
    "type": "object",
    "properties": {
        "confirmado": {"type": "array", "items": {"type": "string"}},
        "dudoso": {"type": "array", "items": {"type": "string"}},
        "contexto": {"type": "array", "items": {"type": "string"}},
        "terminos_actuales": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["confirmado", "dudoso", "contexto", "terminos_actuales"],
}

FACT_SYSTEM = """Eres verificador de datos para un creador de videos en español. Te doy lo que el creador dice en su video y
extractos de fuentes de internet de HOY. Responde solo JSON:
- "confirmado": afirmaciones del creador que las fuentes respaldan (breve, con la fuente entre corchetes, p. ej. [tweaktown.com]).
- "dudoso": afirmaciones del creador que las fuentes contradicen o no respaldan (di qué dicen las fuentes).
- "contexto": 3-6 datos actuales y relevantes de las fuentes que ayudan a no desinformar (fechas, cifras, versiones oficiales).
- "terminos_actuales": cómo se nombra el tema hoy en las fuentes (nombres oficiales, palabras que la gente busca).
No inventes: si las fuentes no dicen nada de algo, no lo pongas como confirmado."""


def fact_check(topic: str, claims: list[str], queries: list[str], progress=lambda p, s: None) -> dict:
    """Busca el tema en internet y contrasta lo que dice el creador. → {queries, sources, confirmado, dudoso, contexto, ...}"""
    if not enabled():
        return {"skipped": "investigación desactivada"}
    from .ai import llm
    queries = [q for q in dict.fromkeys(q.strip() for q in queries if q and q.strip())][:4] or [topic]
    sources: list[dict] = []
    errors = []
    for i, q in enumerate(queries):
        progress(i / max(len(queries), 1) * 0.5, f"Buscando en internet: {q[:60]}")
        for fn in (news, search):
            try:
                for r in fn(q, 4):
                    if r["url"] and r["url"] not in {s["url"] for s in sources}:
                        sources.append({**r, "query": q})
            except ResearchError as exc:
                errors.append(str(exc)[:120])
    if not sources:
        return {"queries": queries, "sources": [], "error": errors[0] if errors else "sin resultados"}
    progress(0.55, "Leyendo las fuentes")
    excerpts = []
    for s in sources[:6]:
        txt = read(s["url"], 2200) or s.get("snippet", "")
        if txt:
            dom = re.sub(r"^https?://(www\.)?", "", s["url"]).split("/")[0]
            excerpts.append(f"[{dom}] {s['title']} ({s.get('date', '')})\n{txt}")
    progress(0.8, "Verificando datos con las fuentes")
    try:
        out = llm.chat_json(db.get_setting("ollama_url"), db.role_model("writer"), FACT_SYSTEM,
                            f"TEMA: {topic}\nLO QUE DICE EL CREADOR:\n- " + "\n- ".join(claims[:10]) +
                            "\n\nFUENTES:\n" + "\n\n".join(excerpts)[:12000], FACT_SCHEMA, num_ctx=16384, max_tokens=1100)
    except llm.LLMError as exc:
        out = {"error": str(exc)[:200]}
    return {"queries": queries, "sources": [{k: s.get(k) for k in ("title", "url", "date", "source")} for s in sources[:8]],
            **out}


def as_prompt(fc: dict | None) -> str:
    """Bloque de texto para los prompts de la IA con lo verificado en internet."""
    if not fc or not (fc.get("confirmado") or fc.get("contexto") or fc.get("dudoso")):
        return ""
    parts = ["VERIFICADO EN INTERNET HOY (úsalo para no desinformar):"]
    if fc.get("confirmado"):
        parts.append("Confirmado: " + "; ".join(fc["confirmado"][:6]))
    if fc.get("dudoso"):
        parts.append("DUDOSO (no lo afirmes en títulos ni textos): " + "; ".join(fc["dudoso"][:5]))
    if fc.get("contexto"):
        parts.append("Contexto actual: " + "; ".join(fc["contexto"][:6]))
    if fc.get("terminos_actuales"):
        parts.append("Términos que se usan hoy: " + ", ".join(fc["terminos_actuales"][:10]))
    return "\n".join(parts) + "\n"
