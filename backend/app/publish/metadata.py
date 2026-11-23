"""Metadatos pensados para cada red, escritos por el modelo «Escritor» y pulidos con reglas fijas.

Lo que de verdad mueve el alcance: el gancho del título/primera línea, palabras clave que la gente busca
(YouTube y TikTok funcionan como buscadores), pocas etiquetas pero relevantes, y un llamado a comentar.
Los hashtags no hacen viral un video por sí solos: sirven para que la plataforma entienda de qué trata.
"""
from __future__ import annotations

import json
import re
import unicodedata
from collections import Counter
from datetime import datetime

from .. import db
from ..ai import llm

CATEGORIES = {"gaming": "20", "entretenimiento": "24", "gente_y_blogs": "22", "comedia": "23", "educacion": "27",
              "ciencia_y_tecnologia": "28", "noticias_y_politica": "25", "tutoriales": "26", "deportes": "17",
              "musica": "10"}

# Hashtags amplios que sí se buscan, por categoría: siempre van 2 para que la plataforma ubique el tema
CATEGORY_TAGS = {"gaming": ["videojuegos", "gaming", "gamer"], "entretenimiento": ["entretenimiento", "humor", "clips"],
                 "comedia": ["humor", "comedia", "risas"], "gente_y_blogs": ["historias", "vlog", "humor"],
                 "educacion": ["aprende", "datoscuriosos", "educacion"], "ciencia_y_tecnologia": ["tecnologia", "ciencia", "tech"],
                 "tutoriales": ["tutorial", "tips", "aprende"], "deportes": ["deportes", "futbol", "deporte"],
                 "musica": ["musica", "music", "cover"], "noticias_y_politica": ["noticias", "actualidad", "mexico"]}

DEFAULT_PROFILE = {"niche": "", "audience": "público hispanohablante", "brand_hashtags": "", "cta": "",
                   "language": "es", "tone": "cercano y con energía"}

SYSTEM = """Eres estratega de contenido para redes sociales en español (YouTube Shorts/largos, TikTok e Instagram Reels).
Escribes metadatos que maximizan clics, retención y búsqueda SIN engañar: el título promete lo que el video cumple.
Básate en lo que se dice y pasa en el video (campo lo_que_se_dice): menciona el momento concreto, no generalidades.

Reglas por red:
- youtube.title: máx. 70 caracteres. NO copies el título de trabajo: reescríbelo como gancho (curiosidad, sorpresa, reto o conflicto) con la palabra clave principal al inicio. Puede llevar 1 emoji al final. Sin MAYÚSCULAS completas (como mucho 1 palabra).
  Ejemplos del estilo buscado: "Doctor revisa la radiografía y no puede creer lo que ve 😳", "Instalé GTA 6 en PC antes que nadie y pasó esto".
- youtube.description: primera línea = gancho + palabra clave (es lo que se ve sin abrir). Luego 2-3 frases naturales que cuenten de qué va. Termina con una pregunta concreta para que comenten. PROHIBIDO poner listas de palabras clave ("Busca:", "Palabras clave:", "Tags:") y hashtags.
- youtube.tags: 10-15 búsquedas reales que alguien escribiría (1 a 4 palabras), de lo específico a lo general. Nunca nombres de plataformas (YouTube, TikTok, Instagram, redes sociales) ni la palabra viral.
- youtube.category: una de: gaming, entretenimiento, gente_y_blogs, comedia, educacion, ciencia_y_tecnologia, noticias_y_politica, tutoriales, deportes, musica.
- tiktok.caption: gancho de máx. 120 caracteres que complemente (no repita) el video, con palabras clave buscables; sin hashtags.
- tiktok.audio_tip: qué tipo de audio en tendencia conviene (ánimo, ritmo y palabras para buscarlo en la biblioteca de sonidos) o si conviene dejar solo el audio original y por qué. 1-2 frases concretas.
- tiktok.search_terms: 3-5 frases que la gente escribiría en el buscador de TikTok para encontrar esto (sin nombres de plataformas).
- instagram.caption: primera línea gancho (máx. 125 caracteres, es lo único que se ve); luego 1-2 frases de valor; termina con una pregunta o llamado a guardar/compartir; sin hashtags.
- instagram.audio_name: nombre corto y llamativo para el audio original del Reel (máx. 40 caracteres).
- hashtags: 8 hashtags sin '#', en minúsculas, sin espacios ni tildes. Cada uno de 1 o 2 palabras que la comunidad realmente usa y busca (ej.: medicina, doctores, humor, historiasreales, gamingenespanol, clipsdestream). Mezcla 2 amplios del tema + 4 de nicho + 2 de la comunidad o formato. PROHIBIDO pegar frases o el título en un hashtag (mal: doctorquevendepaletas) y los genéricos fyp, viral, parati, foryou, trending.
- thumbnail_text: 2-4 palabras para la portada.
Si te doy "verificado_en_internet", respétalo: nunca afirmes lo marcado como DUDOSO y usa los términos actuales.
Los "titulos_del_tema_con_mas_vistas_este_mes" son referencia de lo que funciona HOY: inspírate en su estructura, no los copies.
Escribe en el idioma indicado. Responde solo JSON."""

SCHEMA = {
    "type": "object",
    "properties": {
        "youtube": {"type": "object", "properties": {
            "title": {"type": "string"}, "description": {"type": "string"},
            "tags": {"type": "array", "items": {"type": "string"}}, "category": {"type": "string"}},
            "required": ["title", "description", "tags", "category"]},
        "tiktok": {"type": "object", "properties": {
            "caption": {"type": "string"}, "audio_tip": {"type": "string"},
            "search_terms": {"type": "array", "items": {"type": "string"}}},
            "required": ["caption", "audio_tip", "search_terms"]},
        "instagram": {"type": "object", "properties": {
            "caption": {"type": "string"}, "audio_name": {"type": "string"}},
            "required": ["caption", "audio_name"]},
        "hashtags": {"type": "array", "items": {"type": "string"}},
        "thumbnail_text": {"type": "string"},
    },
    "required": ["youtube", "tiktok", "instagram", "hashtags", "thumbnail_text"],
}

GENERIC_TAGS = {"fyp", "foryou", "foryoupage", "parati", "viral", "fy", "xyzbca", "trending", "tiktok", "reels",
                "instagram", "youtube", "video", "videos", "explore", "explorepage", "paratiii", "fypシ"}

STOP = set("""a al algo ante antes así aunque bien cada como con contra cual cuando de del desde donde dos el ella ellos
en entre era es esa ese eso esta este esto estos fue ha hace hay he la las le les lo los más me mi mis mucho muy nada
ni no nos o otra otro para pero poco por porque pues que qué quien se sea si sí sin sobre son su sus también tan te
tengo tiene todo todos tu tú un una uno unos usted va vamos y ya yo eh ah oye bueno entonces osea o sea pues ok okay
""".split())


def profile() -> dict:
    return {**DEFAULT_PROFILE, **(db.get_setting("publish_profile") or {})}


def slug_tag(t: str) -> str:
    t = unicodedata.normalize("NFKD", str(t)).encode("ascii", "ignore").decode()
    t = re.sub(r"[^0-9a-zA-Z_]", "", t.lower().lstrip("#"))
    return t[:40]


PLATFORM_WORDS = re.compile(r"\b(youtube|tiktok|instagram|reels?|shorts?|redes sociales|viral|fyp)\b", re.I)
KEYWORD_LINE = re.compile(r"^\s*(busca|palabras clave|keywords|tags|etiquetas)\s*:.*$", re.I | re.M)


def _hashtags(raw: list, brand: str, max_len: int = 22) -> list[str]:
    """Hashtags que la gente usa: cortos (los larguísimos son frases pegadas que nadie busca), sin genéricos."""
    out = []
    for t in list(raw or []):
        s = slug_tag(t)
        if 3 <= len(s) <= max_len and s not in GENERIC_TAGS and s not in out:
            out.append(s)
    for t in re.split(r"[\s,]+", brand or ""):  # los de tu marca van siempre, aunque sean largos
        s = slug_tag(t)
        if len(s) >= 2 and s not in out:
            out.append(s)
    return out


def _keywords(text: str, n: int = 8) -> list[str]:
    words = re.findall(r"[a-záéíóúñü0-9]{4,}", (text or "").lower())
    c = Counter(w for w in words if w not in STOP)
    return [w for w, _ in c.most_common(n)]


def _cut(s: str, n: int) -> str:
    s = re.sub(r"\s+", " ", str(s or "")).strip()
    if len(s) <= n:
        return s
    cut = s[:n].rsplit(" ", 1)[0]
    return cut.rstrip(" ,.;:-") + "…"


def _ht(tags: list[str], n: int) -> str:
    return " ".join(f"#{t}" for t in tags[:n])


def finalize(raw: dict, src: dict, prof: dict) -> dict:
    """Aplica límites y buenas prácticas por red y arma los textos finales listos para publicar."""
    yt, tt, ig = raw.get("youtube") or {}, raw.get("tiktok") or {}, raw.get("instagram") or {}
    yt0 = raw.get("youtube") or {}
    cat0 = str(yt0.get("category") or "").strip().lower().replace(" ", "_")
    model_tags = _hashtags(raw.get("hashtags"), "")
    broad = [t for t in CATEGORY_TAGS.get(cat0, CATEGORY_TAGS["entretenimiento"]) if t not in model_tags][:2]
    tags = model_tags[:2] + broad + model_tags[2:]  # 2 de nicho de la IA + 2 amplios + el resto
    brand = _hashtags([], prof.get("brand_hashtags") or "")
    vertical, short = src.get("vertical"), (src.get("duration") or 0) <= 180
    cta = (prof.get("cta") or "").strip()

    # YouTube
    title = _cut(yt.get("title") or src.get("title") or "Video", 95 if not (vertical and short) else 88)
    yt_tags_line = list(dict.fromkeys(tags[:3] + brand[:2]))
    if vertical and short:
        yt_tags_line = ["shorts"] + [t for t in yt_tags_line if t != "shorts"]
    desc = KEYWORD_LINE.sub("", (yt.get("description") or "")).strip()
    desc = re.sub(r"\s*(Busca|Palabras clave)\s*:[^.\n]*[.\n]?", " ", desc).strip()
    desc = re.sub(r"(?:\s*#\w+)+\s*$", "", desc).strip()  # hashtags que la IA haya puesto: se añaden aparte
    if cta and cta.lower() not in desc.lower():
        desc += f"\n\n{cta}"
    desc = (desc + "\n\n" + _ht(yt_tags_line, 5)).strip()
    ytags = []
    for t in list(yt.get("tags") or []):
        t = re.sub(r"\s+", " ", str(t).lstrip("#")).strip()
        years = [int(y) for y in re.findall(r"\b(20\d\d)\b", t)]
        if years and min(years) < datetime.now().year:  # "juegos 2024" envejece mal
            continue
        if t and len(t) <= 60 and not PLATFORM_WORDS.search(t) and t.lower() not in {x.lower() for x in ytags}:
            ytags.append(t)
    cat = str(yt.get("category") or "").strip().lower().replace(" ", "_")
    youtube = {"title": title, "description": desc[:4900], "tags": ytags[:20],
               "category": cat if cat in CATEGORIES else "entretenimiento",
               "category_id": CATEGORIES.get(cat, "24"), "language": prof.get("language") or "es",
               "is_short": bool(vertical and short)}

    # TikTok: 3-5 hashtags (más diluye) + los de tu marca
    tt_tags = list(dict.fromkeys(tags[:4] + brand[:1]))[:5]
    cap = _cut(tt.get("caption") or title, 150)
    tiktok = {"caption": f"{cap}\n\n{_ht(tt_tags, 5)}".strip(), "hashtags": tt_tags,
              "audio_tip": _cut(tt.get("audio_tip") or "", 300),
              "search_terms": [_cut(x, 50) for x in (tt.get("search_terms") or []) if not PLATFORM_WORDS.search(x)][:5]}

    # Instagram: 3-5 hashtags al final
    ig_tags = list(dict.fromkeys(tags[:4] + brand[:1]))[:5]
    body = (ig.get("caption") or cap).strip()
    if cta and cta.lower() not in body.lower():
        body += f"\n\n{cta}"
    instagram = {"caption": f"{body}\n\n{_ht(ig_tags, 5)}"[:2200].strip(), "hashtags": ig_tags,
                 "audio_name": _cut(ig.get("audio_name") or title, 40)}
    return {"youtube": youtube, "tiktok": tiktok, "instagram": instagram, "hashtags": tags,
            "thumbnail_text": _cut(raw.get("thumbnail_text") or "", 40)}


def fallback(src: dict) -> dict:
    """Sin IA disponible: arma algo decente con el título del clip y las palabras más repetidas."""
    kw = _keywords(" ".join([src.get("title") or "", src.get("hook") or "", src.get("text") or ""]))
    t = src.get("title") or "Video"
    return {"youtube": {"title": t, "description": (src.get("hook") or t) + "\n\n¿Qué opinas? Te leo en los comentarios.",
                        "tags": kw, "category": "entretenimiento"},
            "tiktok": {"caption": src.get("hook") or t, "audio_tip": "", "search_terms": kw[:3]},
            "instagram": {"caption": (src.get("hook") or t) + "\n\n¿Qué harías tú? 👇", "audio_name": t[:40]},
            "hashtags": kw, "thumbnail_text": ""}


QUERY_SCHEMA = {"type": "object", "properties": {"tema": {"type": "string"},
                                                "consultas": {"type": "array", "items": {"type": "string"}}},
                "required": ["tema", "consultas"]}


def research_for(src: dict) -> tuple[dict | None, list[dict]]:
    """Verifica el tema en internet (o reutiliza lo que ya verificó la auto-edición) y trae los videos del tema
    con más vistas este mes, para que los títulos sean actuales y no desinformen."""
    from .. import research
    if not research.enabled():
        return None, []
    fc = src.get("research") if (src.get("research") or {}).get("sources") else None
    und = src.get("understood") or {}
    tema = und.get("tema") or ""
    queries = list(und.get("consultas") or [])
    if not tema or not queries:
        try:
            q = llm.chat_json(db.get_setting("ollama_url"), db.role_model("writer"),
                              'Lee lo que se dice en un video y responde solo JSON: "tema" (una frase precisa) y '
                              '"consultas" (2-3 búsquedas cortas para verificar sus datos hoy).',
                              f"Título de trabajo: {src.get('title')}\nLo que se dice: {(src.get('text') or '')[:3000]}",
                              QUERY_SCHEMA, max_tokens=200)
            tema, queries = q.get("tema") or src.get("title") or "", q.get("consultas") or []
        except llm.LLMError:
            tema, queries = src.get("title") or "", [src.get("title") or ""]
    if fc is None:
        try:
            fc = research.fact_check(tema, [x for x in [src.get("hook"), (src.get("text") or "")[:400]] if x], queries)
        except Exception as exc:  # noqa: BLE001
            fc = {"error": str(exc)[:160]}
    trend = []
    try:
        trend = research.trending_videos(queries[0] if queries else tema, 6)
    except Exception:  # noqa: BLE001
        trend = []
    return fc, trend


def generate(src: dict, notes: str = "") -> dict:
    prof = profile()
    fc, trend = research_for(src)
    from .. import research as _rs
    payload = {
        "idioma": prof.get("language") or "es",
        "canal": {"nicho": prof.get("niche"), "audiencia": prof.get("audience"), "tono": prof.get("tone")},
        "video": {"titulo_de_trabajo": src.get("title"), "gancho": src.get("hook"), "por_que_funciona": src.get("reason"),
                  "duracion_s": round(src.get("duration") or 0), "formato": "vertical" if src.get("vertical") else "horizontal",
                  "de_donde_sale": src.get("origin"), "lo_que_se_dice": (src.get("text") or "")[:3500]},
        "indicaciones_del_usuario": notes or None,
        "verificado_en_internet": _rs.as_prompt(fc) or None,
        "titulos_del_tema_con_mas_vistas_este_mes": [f"{t['title']} ({t['views']:,} vistas)" for t in trend[:6]] or None,
    }
    used, err = None, None
    try:
        model = db.role_model("writer")
        raw = llm.chat_json(db.get_setting("ollama_url"), model, SYSTEM, json.dumps(payload, ensure_ascii=False),
                            SCHEMA, num_ctx=8192, max_tokens=1400, timeout=300)
        used = model
    except llm.LLMError as exc:
        raw, err = fallback(src), str(exc)
    out = finalize(raw, src, prof)
    out["model"] = used
    out["sources"] = (fc or {}).get("sources") or []
    out["doubts"] = (fc or {}).get("dudoso") or []
    out["trending"] = trend[:6]
    if err:
        out["warning"] = "La IA no respondió; usé un borrador automático. Revisa y ajusta los textos. " + err[:160]
    return out


# Horarios orientativos de mayor actividad en México/LatAm (hora local). Ajusta con tus propias estadísticas.
BEST_HOURS = {"youtube": [(12, 0), (15, 0), (19, 0)], "tiktok": [(13, 0), (19, 0), (21, 0)],
              "instagram": [(11, 0), (18, 0), (20, 0)]}


def suggest_time(platform: str, after=None):
    from datetime import timedelta
    now = after or datetime.now()
    for day in range(0, 3):
        base = (now + timedelta(days=day)).replace(second=0, microsecond=0)
        for h, m in BEST_HOURS.get(platform, BEST_HOURS["tiktok"]):
            t = base.replace(hour=h, minute=m)
            if t > now + timedelta(minutes=20):
                return t
    return now + timedelta(hours=1)
