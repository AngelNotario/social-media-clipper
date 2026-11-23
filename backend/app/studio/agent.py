"""IA editora (Ollama local): enriquece el primer corte y aplica instrucciones en lenguaje natural.

La IA nunca escribe el timeline directamente: devuelve OPERACIONES que se validan y aplican aquí.
"""
from __future__ import annotations

import re
from typing import Callable

from .. import db
from ..ai import llm
from ..ai.llm import chat_video, video_edit_model
from . import assets
from . import style as style_mod
from . import timeline as tlm


def _llm_cfg() -> dict:
    return {"url": db.get_setting("ollama_url"), "model": db.role_model("director")}


def llm_ready() -> bool:
    cfg = _llm_cfg()
    st = llm.status(cfg["url"])
    return st["available"] and cfg["model"] in st["models"]


def transcript_lines(tl: dict, max_chars: int = 9000) -> str:
    """Transcripción en tiempo del timeline, agrupada en frases."""
    lines, cur, t0 = [], [], None
    for w in tl["words"]:
        if t0 is None:
            t0 = w["start"]
        cur.append(w["word"])
        if w["word"].endswith((".", "?", "!", "…")) or len(cur) >= 18:
            lines.append(f"[{t0:.1f}] {' '.join(cur)}")
            cur, t0 = [], None
    if cur:
        lines.append(f"[{t0:.1f}] {' '.join(cur)}")
    text = "\n".join(lines)
    return text[:max_chars]


# ---------------- operaciones ----------------

OPS = ["remove_range", "set_zoom", "add_text", "update_text", "remove_item", "move_item",
       "add_sfx", "add_music", "set_music_volume", "add_broll", "add_background", "set_style",
       "toggle_captions", "split", "set_transition", "set_chroma", "fix_caption"]
TRANSITIONS = ["cut", "zoom", "flash", "fade", "glitch", "whip", "slide"]

OP_SCHEMA = {
    "type": "object",
    "properties": {
        "message": {"type": "string"},
        "ops": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "op": {"type": "string", "enum": OPS},
                    "id": {"type": "string"},
                    "start": {"type": "number"},
                    "end": {"type": "number"},
                    "time": {"type": "number"},
                    "text": {"type": "string"},
                    "kind": {"type": "string", "enum": ["title", "callout", "lower"]},
                    "asset_id": {"type": "integer"},
                    "volume": {"type": "number"},
                    "zoom": {"type": "number"},
                    "key": {"type": "string"},
                    "value": {"type": ["string", "number", "boolean"]},
                    "enabled": {"type": "boolean"},
                    "transition": {"type": "string", "enum": TRANSITIONS},
                    "color": {"type": "string"},
                    "similarity": {"type": "number"},
                    "bg_video_id": {"type": "integer"},
                    "bg_asset_id": {"type": "integer"},
                    "bg_color": {"type": "string"},
                    "bg_start": {"type": "number"},
                    "bg_volume": {"type": "number"},
                    "subject_scale": {"type": "number"},
                    "subject_x": {"type": "number"},
                    "subject_y": {"type": "number"},
                    "video_id": {"type": "integer"},
                    "src_in": {"type": "number"},
                    "search_query": {"type": "string"},
                    "find": {"type": "string"},
                    "mode": {"type": "string", "enum": ["full", "split", "blur"]},
                    "fit": {"type": "string", "enum": ["cover", "contain"]},
                    "game_x": {"type": "number"},
                },
                "required": ["op"],
            },
        },
    },
    "required": ["message", "ops"],
}

STYLE_KEYS_FOR_AI = ["caption_style", "caption_color", "highlight_color", "caption_position", "caption_size",
                     "caption_uppercase", "caption_max_words", "title_color", "title_bg", "callout_color",
                     "music_volume", "zoom_amount", "caption_font", "broll_volume"]

INSTRUCT_SYSTEM = """Eres el asistente de edición de un editor de video vertical (estilo TikTok/Reels).
El usuario te da una instrucción y tú respondes SOLO con JSON: {"message": "...", "ops": [...]}.
"message": explica en español, en una frase, qué cambiaste (o por qué no se pudo).
Nunca contestes que el video es demasiado largo ni que necesita "varios pasos": aplica en esta respuesta todo lo
que SÍ puedas con las operaciones disponibles (aunque sea parte) y di qué quedó pendiente.
Operaciones disponibles (usa tiempos en segundos del timeline y los id que aparecen en el estado):
- {"op":"remove_range","start":s,"end":e}  corta ese tramo de la toma principal (el resto se recorre).
- {"op":"split","time":t}  divide el segmento de video en t.
- {"op":"set_zoom","start":s,"end":e,"zoom":1.0-1.5}  zoom de los segmentos que tocan ese rango (sin rango = todos).
- {"op":"add_text","start":s,"end":e,"text":"...","kind":"title|callout|lower"}
- {"op":"update_text","id":"t_xxx","text":"...","start":s,"end":e,"kind":"..."}  (solo los campos a cambiar)
- {"op":"fix_caption","find":"texto del subtítulo como aparece en SUBTÍTULOS","text":"texto corregido"}  (o "start":
  segundo del subtítulo en vez de "find"). Corrige un SUBTÍTULO automático (lo que se transcribió de la voz); con
  "text":"" lo quita. Los SUBTÍTULOS NO son textos en pantalla: para corregir un subtítulo usa SIEMPRE fix_caption,
  nunca add_text ni update_text. "El primer subtítulo" = el primero de la lista SUBTÍTULOS.
REGLA DE HONESTIDAD: en "message" describe solo lo que hacen tus ops. Si no puedes hacer lo que piden, deja ops vacío
  y dilo claramente; nunca digas que hiciste algo que no está en ops.
- {"op":"remove_item","id":"..."}  elimina un texto, b-roll, música o efecto por id.
- {"op":"move_item","id":"...","start":s}
- {"op":"add_sfx","asset_id":N,"time":t,"volume":0-1}
- {"op":"add_music","asset_id":N,"start":s,"end":e,"volume":0-1}
- {"op":"set_music_volume","volume":0-1}
- {"op":"add_broll","asset_id":N,"start":s,"end":e}  o con un video de la BIBLIOTECA:
  {"op":"add_broll","video_id":N,"src_in":segundo_del_video,"start":s,"end":e}  (tapa la toma con ese tramo)
- {"op":"set_style","key":"<clave>","value":...}  claves: """ + ", ".join(STYLE_KEYS_FOR_AI) + """
  (colores en formato #RRGGBB; caption_style: karaoke|simple|none; caption_position: bottom|middle|top)
- {"op":"toggle_captions","enabled":true|false}
- {"op":"set_transition","start":s,"end":e,"transition":"cut|zoom|flash|fade|glitch|whip|slide"}  transición de entrada
  de los segmentos de video y tomas de b-roll que empiezan en ese rango (sin rango = todos)
- {"op":"set_chroma","enabled":true,"color":"auto|#RRGGBB","bg_video_id":N,"bg_asset_id":N,"bg_color":"#RRGGBB",
   "bg_start":s,"bg_volume":0-1,"subject_scale":0.3-1,"subject_x":0-1,"subject_y":0-1,"similarity":0.05-0.5}
   pantalla verde/azul (chroma key): quita el fondo de color de la toma y pone detrás un video de la BIBLIOTECA
   (bg_video_id, p. ej. un gameplay), un recurso del catálogo (bg_asset_id) o un color. color "auto" = medir el verde real.
   bg_start = segundo del video de fondo desde donde empieza (sáltate intros/menús); bg_volume 0 = fondo sin sonido.
   subject_scale < 1 hace a la persona más pequeña; subject_x/subject_y la colocan (0.5,1 = centro abajo; 0,1 = esquina
   inferior izquierda). "enabled":false lo quita. Úsalo cuando pidan croma, chroma, pantalla/fondo verde o poner un
   gameplay/video "de fondo".
- {"op":"add_background","search_query":"gta 6 gameplay","start":0,"end":60,"mode":"split"}  o con un video de tu
  BIBLIOTECA: {"op":"add_background","video_id":N,"start":0,"end":60,"mode":"split","volume":0}
  pone un video de fondo (gameplay, imagen, clip) en ese tramo del timeline.
  - search_query: busca en internet y DESCARGA AUTOMÁTICAMENTE algo relacionado con lo que hablas (p. ej. "minecraft parkour", "gta 6 gameplay", "fallout 4 gameplay"). No hace falta que pongas el id.
  - video_id: usa un video que ya tengas subido a la biblioteca (id numérico).
  - mode: "split" = el juego en una franja abajo (vertical) o a la derecha (horizontal) sin tapar tu cámara (funciona SIN pantalla verde); "full" = pantalla completa detrás (necesita chroma/pantalla verde activada); "blur" = fondo desenfocado con tu video encima.
  - fit: "cover" (llena la pantalla) o "contain" (ajusta sin recortar).
  - volume: sonido del fondo (0 = silencioso, recomendado para gameplay de fondo). Default 0.
  - game_x: posición horizontal del juego en modo split (0.5 = centro).
  Si piden "juego de fondo", "gameplay de fondo" o "pon este video atrás", usa add_background. Si no se encuentra nada, devuelve ops: [] y explica por qué en "message".
Reglas: usa SOLO asset_id del catálogo y video_id de la biblioteca; no inventes ids; si la instrucción es ambigua haz el cambio más razonable
y dilo en "message"; si no se puede, devuelve "ops": [] y explica por qué.

## 🎧 REQUISITO MULTIMODAL (ver+escuchar)
Si recibes fotogramas (frames) o un resumen de audio, USARlos siempre para tomar decisiones:
- **Visión:** Los fotogramas muestran la composición real. Si ves un recurso en pantalla ("overlay", "tarjeta"), respétalo o ajústalo.
- **Audio:** Si hay música activa, los cortes deben sincronizarse con el ritmo (pico de energía). Si se escucha un SFX fuerte, considera añadir otro al lado visualmente.
- **Sincronía AV:** Un corte en t debe tener un evento de audio cercano (±0.3s) — música, voz, o SFX. Si no hay, es un corte débil; propón quitarlo o moverlo a un beat real.
- **Umbral mínimo:** La edición resultante debe alcanzar al menos 90% de similitud con el estilo de referencia antes de considerarse lista."""


def library_for_llm(limit: int = 40) -> str:
    with db.get_conn() as conn:
        rows = conn.execute("SELECT id, title, duration FROM videos ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return "; ".join(f"{r['id']}: {r['title']} ({(r['duration'] or 0) / 60:.0f} min)" for r in rows) or "(vacía)"


def _set_chroma(tl: dict, op: dict, source_path: str | None, notes: list[str]) -> None:
    from . import chroma
    ch = chroma.normalize(tl.get("chroma"))
    if op.get("enabled") is False:
        ch["enabled"] = False
        tl["chroma"] = ch
        return
    ch["enabled"] = True
    color = str(op.get("color") or "").strip()
    if color.startswith("#") and len(color) == 7:
        ch["color"] = color.upper()
    elif source_path and (color.lower() == "auto" or not tl.get("chroma")):
        seg = (tl["tracks"]["video"] or [{}])[0]
        det = chroma.detect_color(source_path, float(seg.get("src_in", 0)) + 1.0)
        if det["detected"]:
            ch["color"], ch["similarity"] = det["color"], det["similarity"]
            ch["protect"] = chroma.detect_protect(source_path, ch, chroma.sample_times(tl))
        else:
            notes.append("set_chroma: no detecté un fondo verde/azul claro; uso verde estándar")
    if op.get("similarity") is not None:
        ch["similarity"] = op["similarity"]
    bg = ch["bg"]
    if op.get("bg_video_id"):
        with db.get_conn() as conn:
            ok = conn.execute("SELECT 1 FROM videos WHERE id = ?", (int(op["bg_video_id"]),)).fetchone()
        if ok:
            bg.update(kind="video", video_id=int(op["bg_video_id"]), asset_id=None)
        else:
            notes.append(f"set_chroma: el video {op['bg_video_id']} no está en la biblioteca")
    elif op.get("bg_asset_id"):
        bg.update(kind="asset", asset_id=int(op["bg_asset_id"]), video_id=None)
    elif op.get("bg_color"):
        bg.update(kind="color", color=str(op["bg_color"]))
    for k_op, k in (("bg_start", "start"), ("bg_volume", "volume")):
        if op.get(k_op) is not None:
            bg[k] = op[k_op]
    for k_op, k in (("subject_scale", "scale"), ("subject_x", "x"), ("subject_y", "y")):
        if op.get(k_op) is not None:
            ch["subject"][k] = op[k_op]
    tl["chroma"] = chroma.normalize(ch)


def apply_ops(tl: dict, st: dict, ops: list[dict], duration_hint: float | None = None,
              source_path: str | None = None) -> tuple[dict, list[str]]:
    """Aplica operaciones validadas. Devuelve el estilo (posiblemente modificado) y notas de lo que se omitió."""
    notes: list[str] = []
    valid_assets = {a["id"]: a for a in assets.list_assets() if a["available"]}
    dur = tl["duration"] or duration_hint or 0

    def clamp(t, lo=0.0, hi=None):
        hi = dur if hi is None else hi
        return max(lo, min(float(t), hi))

    for op in ops:
        kind = op.get("op")
        try:
            if kind == "remove_range":
                removed = tlm.remove_range(tl, clamp(op["start"]), clamp(op["end"]))
                tl["duration"] -= removed
                dur = tl["duration"]
                # recomputar inicios
                t = 0.0
                for s in tl["tracks"]["video"]:
                    s["start"] = t
                    t += tlm.seg_len(s)
            elif kind == "split":
                tlm.split_at(tl, clamp(op["time"]))
            elif kind == "set_zoom":
                z = max(1.0, min(float(op.get("zoom", 1.1)), 1.5))
                a, b = op.get("start"), op.get("end")
                for s in tl["tracks"]["video"]:
                    s_end = s["start"] + tlm.seg_len(s)
                    if a is None or b is None or (s_end > float(a) and s["start"] < float(b)):
                        s["zoom"] = z
            elif kind == "add_text":
                text = str(op.get("text", "")).strip()
                if not text:
                    raise ValueError("texto vacío")
                a = clamp(op.get("start", 0))
                b = clamp(op.get("end", a + 2.5))
                if b - a < 0.3:
                    b = clamp(a + 2.0)
                tl["tracks"]["text"].append({"id": tlm.nid("t"), "start": a, "end": b, "text": text[:140],
                                             "kind": op.get("kind") if op.get("kind") in tlm.TEXT_KINDS else "callout"})
            elif kind == "fix_caption":
                from ..editor.subtitles import group_words
                groups = group_words(tl.get("words") or [], int(st.get("caption_max_words") or 3))
                if not groups:
                    raise ValueError("este video no tiene subtítulos")
                g = None
                if op.get("find"):
                    g = _match_caption(groups, str(op["find"]))
                if g is None and op.get("start") is not None:
                    t = float(op["start"])
                    g = next((x for x in groups if x[0]["start"] - 0.05 <= t <= x[-1]["end"] + 0.05), None) or \
                        min(groups, key=lambda x: abs(x[0]["start"] - t))
                if g is None:
                    raise ValueError(f"no encontré el subtítulo «{op.get('find', '')}»")
                if not tlm.add_word_fix(tl, g[0]["start"], g[-1]["end"], str(op.get("text") or "")):
                    raise ValueError("no pude ubicar ese subtítulo en la toma")
            elif kind == "update_text":
                track, it = tlm.find_item(tl, op["id"])
                if track != "text":
                    raise ValueError(f"no existe el texto {op.get('id')}")
                if op.get("text"):
                    it["text"] = str(op["text"]).strip()[:140]
                if op.get("kind") in tlm.TEXT_KINDS:
                    it["kind"] = op["kind"]
                if "start" in op:
                    it["start"] = clamp(op["start"])
                if "end" in op:
                    it["end"] = clamp(op["end"])
            elif kind == "remove_item":
                track, it = tlm.find_item(tl, op["id"])
                if not track or track == "video":
                    raise ValueError(f"no existe {op.get('id')}")
                tl["tracks"][track] = [x for x in tl["tracks"][track] if x["id"] != op["id"]]
            elif kind == "move_item":
                track, it = tlm.find_item(tl, op["id"])
                if not track or track == "video":
                    raise ValueError(f"no existe {op.get('id')}")
                length = it.get("end", it["start"]) - it["start"]
                it["start"] = clamp(op["start"])
                if "end" in it:
                    it["end"] = clamp(it["start"] + length)
            elif kind == "add_sfx":
                a = valid_assets.get(int(op["asset_id"]))
                if not a or a["kind"] != "sfx":
                    raise ValueError("efecto no encontrado en la biblioteca")
                tl["tracks"]["sfx"].append({"id": tlm.nid("s"), "asset_id": a["id"],
                                            "start": clamp(op.get("time", op.get("start", 0))),
                                            "volume": max(0.0, min(float(op.get("volume", 0.6)), 1.5))})
            elif kind == "add_music":
                a = valid_assets.get(int(op["asset_id"]))
                if not a or a["kind"] != "music":
                    raise ValueError("música no encontrada en la biblioteca")
                tl["tracks"]["music"].append({"id": tlm.nid("m"), "asset_id": a["id"],
                                              "start": clamp(op.get("start", 0)), "end": clamp(op.get("end", dur)),
                                              "volume": max(0.0, min(float(op.get("volume", st["music_volume"])), 1.0))})
            elif kind == "set_music_volume":
                v = max(0.0, min(float(op["volume"]), 1.0))
                for m in tl["tracks"]["music"]:
                    m["volume"] = v
            elif kind == "add_broll":
                s0 = clamp(op.get("start", 0))
                e0 = clamp(op.get("end", s0 + 3))
                if e0 - s0 < 0.5:
                    raise ValueError("toma de b-roll demasiado corta")
                if op.get("video_id"):  # un tramo de uno de tus videos de la biblioteca (gameplay, otra toma…)
                    with db.get_conn() as conn:
                        v = conn.execute("SELECT id, duration FROM videos WHERE id = ?", (int(op["video_id"]),)).fetchone()
                    if not v:
                        raise ValueError("ese video no está en la biblioteca")
                    vd = float(v["duration"] or 0)
                    src_in = max(0.0, min(float(op.get("src_in") or 0), max(0.0, vd - (e0 - s0))))
                    tl["tracks"]["broll"].append({"id": tlm.nid("b"), "video_id": v["id"], "start": s0, "end": e0,
                                                  "src_in": round(src_in, 2), "transition": "fade"})
                else:
                    a = valid_assets.get(int(op["asset_id"]))
                    if not a or a["kind"] != "broll":
                        raise ValueError("b-roll no encontrado en la biblioteca")
                    tl["tracks"]["broll"].append({"id": tlm.nid("b"), "asset_id": a["id"], "start": s0,
                                                  "end": e0, "src_in": 0.0, "transition": "fade"})
            elif kind == "set_style":
                key = op.get("key")
                if key not in STYLE_KEYS_FOR_AI:
                    raise ValueError(f"clave de estilo no permitida: {key}")
                st = style_mod.clean_style({**st, key: op.get("value")})
            elif kind == "set_transition":
                tr = op.get("transition")
                if tr not in TRANSITIONS:
                    raise ValueError(f"transición desconocida: {tr}")
                a, b = op.get("start"), op.get("end")
                for track in ("video", "broll"):
                    for it in tl["tracks"][track]:
                        if a is None or b is None or float(a) - 0.05 <= it["start"] <= float(b) + 0.05:
                            it["transition"] = tr
            elif kind == "toggle_captions":
                tl["captions"]["enabled"] = bool(op.get("enabled", True))
            elif kind == "set_chroma":
                _set_chroma(tl, op, source_path, notes)
            elif kind == "add_background":
                s0 = clamp(op.get("start", 0))
                e0 = clamp(op.get("end", s0 + 5))
                if e0 - s0 < 0.5:
                    raise ValueError("el fondo es demasiado corto")
                mode = op.get("mode", "full")
                fit = op.get("fit", "cover")
                vol = max(0.0, min(float(op.get("volume", 0.0)), 1.0))
                game_x = max(0.0, min(float(op.get("game_x", 0.5)), 1.0))

                bg_entries = []
                if op.get("search_query"):
                    from ..resources.finder import find as _find
                    need = {"entity": str(op["search_query"]), "what": "juego",
                            "query": str(op["search_query"]), "t0": s0, "t1": e0,
                            "label": str(op["search_query"])[:50], "priority": 3}
                    r = _find(need, allow_web=True)
                    if r and r.get("asset_id"):
                        bg_entries.append({"asset_id": r["asset_id"], "start": s0, "end": e0,
                                           "src_in": 0.0, "mode": "full", "fit": fit,
                                           "volume": vol, "anim": "fade",
                                           "label": str(op["search_query"])[:32], "gen": True})
                    else:
                        notes.append(f"add_background: no se encontró recurso para «{op['search_query']}»")
                elif op.get("video_id"):
                    with db.get_conn() as conn:
                        v = conn.execute("SELECT id, duration FROM videos WHERE id = ?",
                                         (int(op["video_id"]),)).fetchone()
                    if not v:
                        raise ValueError("ese video no está en la biblioteca")
                    vd = float(v["duration"] or 0)
                    src_in = max(0.0, min(float(op.get("src_in") or 0),
                                          max(0.0, vd - (e0 - s0))))
                    bg_entries.append({"video_id": v["id"], "start": s0, "end": e0,
                                       "src_in": round(src_in, 2), "mode": "full", "fit": fit,
                                       "volume": vol, "anim": "fade", "gen": True})
                else:
                    raise ValueError("add_background necesita video_id o search_query")

                if mode == "full":
                    ch = chroma.normalize(tl.get("chroma"))
                    ch["enabled"] = True
                    bg = ch["bg"]
                    if bg_entries:
                        e = bg_entries[0]
                        if e.get("video_id"):
                            bg.update(kind="video", video_id=e["video_id"], asset_id=None,
                                      start=s0, volume=vol, fit=fit)
                        elif e.get("asset_id"):
                            bg.update(kind="asset", asset_id=e["asset_id"], video_id=None,
                                      start=s0, volume=vol, fit=fit)
                    if not ch.get("protect"):  # con el recuadro de OBS protegido no se encoge a la persona
                        ch["subject"] = {"scale": 0.5, "x": 0.5, "y": 0.9}
                    tl["chroma"] = chroma.normalize(ch)
                else:
                    # split / blur sin pantalla verde: el juego va en una franja (abajo en vertical, a la derecha en
                    # horizontal) y tu toma queda intacta. Antes el b-roll iba a pantalla completa y tapaba tu cámara.
                    vertical = int(tl.get("height") or 1920) > int(tl.get("width") or 1080)
                    for e in bg_entries:
                        e.update(mode="pip", frame="none", rot=0, anim="fade", kenburns=False,
                                 pos={"x": 0.5, "y": 0.76} if vertical else {"x": 0.75, "y": 0.5},
                                 scale=1.0 if vertical else 0.5)
                    tl["tracks"]["broll"].extend(bg_entries)
                    if mode == "blur":
                        notes.append("add_background: el modo blur se aplicó como franja (no hay desenfoque de fondo "
                                     "sin pantalla verde)")
            else:
                raise ValueError(f"operación desconocida: {kind}")
        except (KeyError, TypeError, ValueError) as exc:
            notes.append(f"{kind}: {exc}")
    return st, notes


def _norm_txt(t: str) -> str:
    import unicodedata
    t = unicodedata.normalize("NFKD", t.lower()).encode("ascii", "ignore").decode()
    return " ".join(re.sub(r"[^a-z0-9 ]", " ", t).split())


def _match_caption(groups: list[list[dict]], find: str):
    """El subtítulo cuyo texto se parece más a `find` (sin acentos ni signos; tolera errores de dedo)."""
    import difflib
    want = _norm_txt(find)
    if not want:
        return None
    best, score = None, 0.0
    for g in groups:
        txt = _norm_txt(" ".join(w["word"] for w in g))
        r = 1.0 if want == txt else 0.95 if (want in txt or (txt and txt in want)) else \
            difflib.SequenceMatcher(None, want, txt).ratio()
        if r > score:
            best, score = g, r
    return best if score >= 0.6 else None


def captions_for_llm(tl: dict, st: dict, max_chars: int = 2500) -> str:
    from ..editor.subtitles import group_words
    out = []
    for g in group_words(tl.get("words") or [], int(st.get("caption_max_words") or 3)):
        out.append(f"{g[0]['start']:.2f}-{g[-1]['end']:.2f}: {' '.join(w['word'] for w in g)}")
    return "\n".join(out)[:max_chars] or "(sin subtítulos)"


def instruct(tl: dict, st: dict, instruction: str, source_path: str | None = None) -> tuple[dict, dict, str, list[str]]:
    cfg = _llm_cfg()
    cat = assets.catalog_for_llm()
    by_id = {a["id"]: a for a in assets.list_assets()}
    user = (f"INSTRUCCIÓN DEL USUARIO: {instruction}\n\n"
            f"ESTADO DEL TIMELINE:\n{tlm.summary_for_llm(tl, by_id)}\n\n"
            f"ESTILO ACTUAL: { {k: st[k] for k in STYLE_KEYS_FOR_AI} }\n\n"
            f"CATÁLOGO DE RECURSOS (id, tipo, título, etiquetas): {cat[:40]}\n\n"
            f"VIDEOS DE LA BIBLIOTECA (para fondo de chroma; id: título): {library_for_llm()}\n\n"
            f"SUBTÍTULOS (inicio-fin: texto, en el orden en que salen):\n{captions_for_llm(tl, st)}\n\n"
            f"TRANSCRIPCIÓN (tiempo del timeline):\n{transcript_lines(tl, 3500)}")
    try:
        data = llm.chat_json(cfg["url"], cfg["model"], INSTRUCT_SYSTEM, user, OP_SCHEMA, num_ctx=16384, timeout=900)
    except llm.LLMError:  # el modelo tardó demasiado (p. ej. recién cargado tras el de visión): versión corta
        short = (f"INSTRUCCIÓN DEL USUARIO: {instruction}\n\n"
                 f"ESTADO DEL TIMELINE:\n{tlm.summary_for_llm(tl, by_id)[:2500]}\n\n"
                 f"ESTILO ACTUAL: { {k: st[k] for k in STYLE_KEYS_FOR_AI} }\n\n"
                 f"SUBTÍTULOS (inicio-fin: texto):\n{captions_for_llm(tl, st, 1500)}\n\n"
                 f"TRANSCRIPCIÓN (tiempo del timeline):\n{transcript_lines(tl, 2000)}")
        data = llm.chat_json(cfg["url"], cfg["model"], INSTRUCT_SYSTEM, short, OP_SCHEMA, num_ctx=8192, timeout=900)
    ops = data.get("ops", []) or []
    st2, notes = apply_ops(tl, st, ops, source_path=source_path)
    msg = str(data.get("message", "")).strip()[:400]
    failed = sum(1 for n in notes if any(n.startswith(f"{o.get('op')}:") for o in ops))
    if not ops:  # que nunca diga "listo" sin haber cambiado nada
        msg = "No hice ningún cambio. " + msg
    elif failed >= len(ops):
        msg = "No pude aplicar el cambio: " + "; ".join(notes)[:300]
    return tl, st2, msg, notes


# ---------------- enriquecimiento del primer corte ----------------

ENRICH_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "callouts": {"type": "array", "items": {"type": "object", "properties": {
            "start": {"type": "number"}, "end": {"type": "number"}, "text": {"type": "string"}},
            "required": ["start", "end", "text"]}},
        "broll": {"type": "array", "items": {"type": "object", "properties": {
            "asset_id": {"type": "integer"}, "video_id": {"type": "integer"}, "src_in": {"type": "number"},
            "start": {"type": "number"}, "end": {"type": "number"}},
            "required": ["start", "end"]}},
        "music_asset_id": {"type": "integer"},
        "emphasis": {"type": "array", "items": {"type": "object", "properties": {
            "time": {"type": "number"}, "sfx_asset_id": {"type": "integer"}}, "required": ["time"]}},
    },
    "required": ["title", "callouts", "broll", "emphasis"],
}

ENRICH_SYSTEM = """Eres un editor de videos virales con criterio propio (nada genérico).
Recibes la transcripción de la toma del creador (con tiempos del timeline) y un catálogo de recursos locales.
Devuelve SOLO JSON con:
- "title": título gancho para los primeros 3 segundos (máx. 7 palabras, sin hashtags, fiel a lo que se dice).
- "callouts": textos destacados en pantalla en los momentos clave (palabras o cifras importantes, máx. 5 palabras cada uno, {n} como máximo). Usa los tiempos donde se dicen.
- "broll": recursos de tipo broll del catálogo que ilustren lo que se dice (solo si encajan de verdad; 2-6 s cada uno; puede ir vacío).
- "music_asset_id": id de una música del catálogo que encaje con el tono (omítelo si no hay música adecuada).
- "emphasis": momentos de énfasis fuerte para un efecto de sonido (time) con "sfx_asset_id" de tipo sfx (impacto, pop, ding, riser...). Máximo {m}.
No inventes ids que no estén en el catálogo."""


UNDERSTAND_SCHEMA = {
    "type": "object",
    "properties": {
        "tema": {"type": "string"},
        "postura": {"type": "string"},
        "hechos": {"type": "array", "items": {"type": "string"}},
        "no_confundir": {"type": "array", "items": {"type": "string"}},
        "consultas": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["tema", "postura", "hechos", "no_confundir", "consultas"],
}

UNDERSTAND_SYSTEM = """Eres un editor que primero ENTIENDE el video antes de tocarlo. Lee la transcripción completa (puede tener
errores de reconocimiento de voz) y responde solo JSON:
- "tema": de qué trata el video en una frase, con precisión (quién, qué problema, dónde).
- "postura": qué opina o denuncia el creador.
- "hechos": 4-8 afirmaciones concretas que el creador dice (cifras, fechas, nombres, quejas), tal como las dice.
- "no_confundir": malentendidos fáciles que un editor podría cometer y que serían FALSOS según lo que dice
  (p. ej. si dice que el juego SÍ salió pero la tienda no entrega las claves, NO es que el juego no salió).
- "consultas": 2-4 búsquedas cortas para verificar en internet los datos clave del video hoy (nombres propios, sin relleno).
Distingue con cuidado lo que afirma de lo que niega. No inventes nada que no se diga."""

VERIFY_SCHEMA = {"type": "object", "properties": {"ok": {"type": "boolean"}, "titulo": {"type": "string"}},
                 "required": ["ok", "titulo"]}


def _norm_words(text: str) -> list[str]:
    import re
    import unicodedata
    t = unicodedata.normalize("NFKD", text.lower()).encode("ascii", "ignore").decode()
    return [w for w in re.findall(r"[a-z0-9]+", t) if len(w) > 2]


def callout_is_said(tl: dict, text: str, start: float, window: float = 8.0) -> bool:
    """Un texto destacado solo vale si lo que dice se escucha en ese momento (evita inventos de la IA)."""
    want = _norm_words(text)
    if not want:
        return False
    near = set()
    for w in tl["words"]:
        if start - window <= w["start"] <= start + window:
            near.update(_norm_words(w["word"]))
    hit = sum(1 for w in want if w in near or any(n.startswith(w[:5]) for n in near if len(w) >= 5))
    return hit / len(want) >= 0.6


def understand(tl: dict, extra: dict) -> dict | None:
    """Paso 1: entender de qué va el video (tema, postura, hechos y qué no confundir)."""
    cfg = _llm_cfg()
    user = (f"NOMBRE DEL PROYECTO (lo puso el creador, es una pista del tema): {extra.get('project_name') or '-'}\n"
            + (f"INSTRUCCIONES DEL CREADOR: {extra['instructions']}\n" if extra.get("instructions") else "")
            + f"TRANSCRIPCIÓN:\n{transcript_lines(tl, 12000)}")
    try:
        return llm.chat_json(cfg["url"], cfg["model"], UNDERSTAND_SYSTEM, user, UNDERSTAND_SCHEMA, num_ctx=16384,
                             max_tokens=900)
    except llm.LLMError:
        return None


def spread_broll(tl: dict, rows: list[dict], every: float, length: float = 4.0) -> int:
    """Reparte tramos de los videos adjuntos como b-roll en los cambios de toma, cada ~`every` segundos,
    sin pisar el título ni otro b-roll. Toma tramos distintos de cada video (sin la intro)."""
    if not rows or every <= 0:
        return 0
    taken = [(b["start"], b["end"]) for b in tl["tracks"]["broll"]]
    cuts = [s["start"] for s in tl["tracks"]["video"][1:]]
    n, last, k = 0, -every, 0
    for t in cuts:
        if t < 6 or t - last < every or t + length > tl["duration"] - 2:
            continue
        if any(a - 1 < t + length and t < b + 1 for a, b in taken):
            continue
        r = rows[k % len(rows)]
        vd = float(r.get("duration") or 0)
        span = max(0.0, vd * 0.9 - length)
        src = vd * 0.05 + (span * ((k * 0.37) % 1.0))
        tl["tracks"]["broll"].append({"id": tlm.nid("b"), "video_id": r["video_id"], "start": round(t, 2),
                                      "end": round(t + length, 2), "src_in": round(src, 1), "transition": "fade"})
        taken.append((t, t + length))
        last, k, n = t, k + 1, n + 1
    return n


def enrich(tl: dict, st: dict, progress: Callable[[float, str], None] = lambda p, s: None,
           extra: dict | None = None) -> dict:
    """Añade título, textos destacados, b-roll, música y énfasis. Si no hay IA, añade lo básico."""
    extra = extra or {}
    dur = tl["duration"]
    broll_rows = [r for r in extra.get("attachments") or [] if r.get("role") in ("broll", "auto")]
    if not tl["words"]:
        return {"used": False, "reason": "sin voz"}
    music = [a for a in assets.list_assets("music") if a["available"]]
    pop = assets.find_by_tag("sfx", "pop")

    if not llm_ready():
        first = []
        for w in tl["words"]:
            first.append(w["word"])
            if w["word"].endswith((".", "?", "!")) or len(first) >= 7:
                break
        tl["tracks"]["text"].append({"id": tlm.nid("t"), "start": 0.0, "end": min(3.0, dur),
                                     "text": " ".join(first).strip(" ,"), "kind": "title"})
        spread_broll(tl, broll_rows, float(extra.get("broll_every_s") or 0))
        return {"used": False, "reason": "Ollama no disponible: se añadió un título básico"}

    progress(0.05, "La IA está entendiendo de qué trata tu video")
    gist = understand(tl, extra)
    fc = None
    if gist and extra.get("research", True):
        from .. import research
        try:  # verificar en internet antes de escribir nada (si falla la red, se sigue sin esto)
            fc = research.fact_check(gist["tema"], gist.get("hechos") or [], gist.get("consultas") or [gist["tema"]],
                                     lambda x, s: progress(0.08 + 0.18 * x, s))
        except Exception as exc:  # noqa: BLE001
            fc = {"error": str(exc)[:160]}
        extra["research_result"] = fc
    progress(0.3, "La IA está escribiendo el título y los textos")
    n_callouts = max(1, round(st["callouts_per_min"] * dur / 60))
    system = ENRICH_SYSTEM.replace("{n}", str(n_callouts)).replace("{m}", str(max(1, n_callouts // 2)))
    cat = assets.catalog_for_llm()
    kind = "video largo para YouTube" if extra.get("video_type") == "long" else "video corto vertical (TikTok/Reels/Shorts)"
    every = float(extra.get("broll_every_s") or 0)
    brief_txt = (f"TIPO DE VIDEO: {kind}. ESTILO DE EDICIÓN: {extra.get('edit_style') or 'viral'}.\n"
                 + ("Ritmo de video largo: textos solo en datos/ideas clave, nada de relleno; " if extra.get("video_type") == "long" else "")
                 + (f"b-roll aprox. cada {every:.0f} s (4-6 s cada uno) cuando ayude a ilustrar.\n" if every else "Sin b-roll.\n")
                 + (f"INSTRUCCIONES DEL CREADOR: {extra['instructions']}\n" if extra.get("instructions") else "")
                 + (f"VIDEOS ADJUNTOS DE SU BIBLIOTECA (úsalos como b-roll con video_id y src_in = segundo del video "
                    f"del que tomar el tramo; elige tramos variados, no la intro):\n{extra['attachments_text']}\n"
                    if extra.get("attachments_text") else ""))
    gist_txt = ""
    if gist:
        gist_txt = (f"LO QUE DICE EL VIDEO (verificado, respétalo):\nTema: {gist['tema']}\nPostura: {gist['postura']}\n"
                    f"Hechos: {'; '.join(gist['hechos'][:8])}\nNO CONFUNDIR (sería falso): {'; '.join(gist['no_confundir'][:6])}\n"
                    "El título y los textos deben ser fieles a esto. Los textos destacados deben ser palabras o cifras que "
                    "el creador DICE en ese momento (cópialas casi literal).\n")
    from .. import research as _rs
    gist_txt += _rs.as_prompt(fc)
    user = (f"{brief_txt}{gist_txt}Duración del video: {dur:.1f}s.\nCATÁLOGO: {cat}\n\nTRANSCRIPCIÓN:\n{transcript_lines(tl)}")
    cfg = _llm_cfg()
    data = llm.chat_json(cfg["url"], cfg["model"], system, user, ENRICH_SCHEMA)
    progress(0.85, "Colocando elementos en el timeline")

    ops: list[dict] = []
    title = str(data.get("title", "")).strip()
    if title and gist:  # paso 3: comprobar que el título no contradice lo que dice el creador
        try:
            v = llm.chat_json(cfg["url"], cfg["model"],
                              "Revisas títulos de video. Responde solo JSON {\"ok\": bool, \"titulo\": str}. "
                              "ok=false si el título contradice, exagera algo falso o confunde el tema; en ese caso escribe "
                              "en \"titulo\" uno fiel (máx. 7 palabras, mismo idioma). Si está bien, repite el título.",
                              f"Tema: {gist['tema']}\nPostura: {gist['postura']}\nNo confundir: {'; '.join(gist['no_confundir'][:6])}"
                              + (f"\nDudoso según internet (no afirmarlo): {'; '.join((fc or {}).get('dudoso', [])[:4])}" if (fc or {}).get("dudoso") else "")
                              + f"\nTítulo propuesto: {title}", VERIFY_SCHEMA, max_tokens=120)
            if not v.get("ok") and str(v.get("titulo") or "").strip():
                title = str(v["titulo"]).strip()
        except llm.LLMError:
            pass
    long_video = extra.get("video_type") == "long"
    if title:
        ops.append({"op": "add_text", "start": 0.0, "end": min(3.0, dur), "text": title, "kind": "title"})
    dropped = 0
    for c in (data.get("callouts") or [])[:n_callouts]:
        if c.get("start", 0) < 2.8:  # no tapar el título
            continue
        if not callout_is_said(tl, str(c.get("text", "")), float(c.get("start", 0))):
            dropped += 1  # la IA puso algo que no se dice ahí: fuera
            continue
        end = c.get("end", c["start"] + (3.0 if long_video else 1.8))
        # videos largos: rótulo pequeño abajo (no un texto gigante encima de tu cara)
        ops.append({"op": "add_text", "start": c["start"], "end": max(end, c["start"] + (2.5 if long_video else 1.2)),
                    "text": c.get("text", ""), "kind": "lower" if long_video else "callout"})
        if pop and not long_video:
            ops.append({"op": "add_sfx", "asset_id": pop["id"], "time": c["start"], "volume": 0.5})
    allowed_vids = {r["video_id"] for r in broll_rows}
    for b in data.get("broll") or []:
        if b.get("video_id") and b["video_id"] not in allowed_vids:
            continue  # solo videos que el creador adjuntó para b-roll
        if not b.get("video_id") and not b.get("asset_id"):
            continue
        ops.append({"op": "add_broll", **b})
    mid = data.get("music_asset_id")
    if st["music_volume"] <= 0 or extra.get("music") == "off":
        pass
    elif mid and any(a["id"] == mid for a in music):  # solo una música que la IA eligió por el tono; nunca "la primera"
        ops.append({"op": "add_music", "asset_id": mid, "start": 0.0, "end": dur, "volume": st["music_volume"]})
    for e in (data.get("emphasis") or [])[:max(1, n_callouts // 2)]:
        if e.get("sfx_asset_id"):
            ops.append({"op": "add_sfx", "asset_id": e["sfx_asset_id"], "time": e["time"], "volume": 0.6})
    _, notes = apply_ops(tl, st, ops)
    if broll_rows and every:  # completa el ritmo pedido con tramos de los videos adjuntos
        want = int(dur // every)
        have = sum(1 for b in tl["tracks"]["broll"] if b.get("video_id"))
        if have < want * 0.6:
            spread_broll(tl, broll_rows, every)
    return {"used": True, "understood": gist, "research": fc, "dropped_callouts": dropped, "title": title, "texts": len(tl["tracks"]["text"]), "broll": len(tl["tracks"]["broll"]),
            "music": bool(tl["tracks"]["music"]), "skipped": notes}


# ---------------- agente de edición multimodal (ver + escuchar) ----------------

def _frames_for_agent(src_path: str | None, tl: dict, count: int = 5) -> list[tuple[float, str]]:
    """Extrae hasta `count` fotogramas del video fuente y los retorna como [(timestamp, b64png)]."""
    if not src_path or not Path(src_path).exists():
        return []
    from ..ai import llm as ai_llm  # pyright: ignore[reportUnknownVariableType]
    frames_b64: list[str] = []
    times: list[float] = []
    try:
        # calcular tiempos equidistantes dentro del timeline
        dur = tl.get("duration") or 0
        span = max(dur / count, 1.0)
        times = [round(i * span + span / 2, 1) for i in range(count)]
        import subprocess
        cmd = (f'ffmpeg -i "{src_path}" -vf "scale=480:320" '
               '-skip_frame nokey -vsync vfr '
               '-frames:v 5 '
               '-f image2pipe -pix_fmt rgb24 pipe:1')
        proc = subprocess.run(cmd, capture_output=True, timeout=30)
        if proc.returncode != 0 or not proc.stdout:
            return []
        # extraer solo los fotogramas KEYFRAME (ffmpeg los separa por headers jpg/png)
        chunks = proc.stdout.split(b'\xff\xd8')[1:]  # cada JPG empieza con \xff\xd8
        for chunk in chunks[:count]:
            b64 = __import__("base64").b64encode(b'\xff\xd8' + chunk).decode("ascii")
            frames_b64.append(b64)
    except Exception:  # noqa: BLE001
        return []
    if not frames_b64:
        # fallback: ffmpeg frame-by-frame a un temp dir, luego leer
        tmp_dir = config.WORK_DIR / "_agent_frames"
        tmp_dir.mkdir(exist_ok=True)
        try:
            for t in times[:count]:
                fp = tmp_dir / f"frame_{t:.1f}.jpg"
                subprocess.run(
                    [config.FFMPEG, "-i", src_path, "-ss", str(t), "-frames:v", "1", "-vf",
                     "scale=480:320", "-y", str(fp)],
                    capture_output=True, timeout=15)
                if fp.exists() and fp.stat().st_size > 100:
                    frames_b64.append(fp.read_bytes().hex())  # will be converted below
        except Exception:  # noqa: BLE001
            pass
    return list(zip(times[:len(frames_b64)], frames_b64))


def instruct_video(tl: dict, p: dict, instruction: str, frames_path: list[str] | None = None,
                   audio_summary: str | None = None, source_path: str | None = None) -> tuple[dict, dict, str, list[str]]:
    """Versión multimodal de instruct(): usa fotogramas reales del video y un resumen de audio para tomar decisiones de edición.

    Este es el motor donde la IA puede VER y ESCUCHAR — no solo leer texto/transcripción.
    Se usa en el bucle de refinamiento de estilo (≥90%) para que los agentes evalúen calidad visual + auditiva.

    Args:
        tl: timeline actual.
        p: proyecto (con source_path, style, etc.).
        instruction: qué quiere el usuario.
        frames_path: lista de rutas a fotogramas JPG/PNG ya extraídos.
        audio_summary: resumen de lo que se escucha ("voz clara, música electrónica 120bpm, pop a los 3s").
        source_path: ruta al video fuente (se usan frames del timeline si no se pasan frames_path).
    """
    # Obtener fotogramas
    fps = frames_path if frames_path else []
    if not fps and source_path and Path(source_path).exists():
        fps = [source_path]  # chat_video usará el video directo (ffmpeg internal)

    cfg = _llm_cfg()
    cat = assets.catalog_for_llm()
    by_id = {a["id"]: a for a in assets.list_assets()}
    timeline_ctx = tlm.summary_for_llm(tl, by_id)
    style_ctx = {k: st[k] for k in STYLE_KEYS_FOR_AI} if (st := p.get("style")) else {}

    user_parts = [
        f"INSTRUCCIÓN DEL USUARIO: {instruction}",
        f"ESTADO DEL TIMELINE:\n{timeline_ctx}",
        f"ESTILO ACTUAL: {style_ctx}",
        f"CATÁLOGO DE RECURSOS: {cat[:30]}",
        f"VIDEOS DE LA BIBLIOTECA: {library_for_llm()}",
    ]

    if audio_summary:
        user_parts.append(f"LO QUE SE ESCUCHA (audio): {audio_summary}")

    model = video_edit_model(cfg["url"]) or cfg["model"]

    try:
        data = chat_video(
            cfg["url"], model,
            INSTRUCT_SYSTEM,  # ahora incluye sección multimodal
            "\n".join(user_parts),
            OP_SCHEMA,
            frames_b64=fps,
            audio_summary=audio_summary,
            timeout=900,
            num_ctx=16384,
        )
    except llm.LLMError:
        # fallback: instruct normal (sin visión/audio)
        return instruct(tl, p.get("style") if isinstance(p, dict) else {}, instruction, source_path=source_path)

    ops = data.get("ops", []) or []
    notes_list: list[str] = []
    st2 = p.get("style", {}) if isinstance(p, dict) else {}
    st2, notes = apply_ops(tl, st2, ops, source_path=source_path)
    msg = str(data.get("message", "")).strip()[:400]
    failed = sum(1 for n in notes if any(n.startswith(f"{o.get('op')}:") for o in ops))
    if not ops:
        msg = "No hice ningún cambio. " + msg
    elif failed >= max(len(ops), 1):
        msg = "No pude aplicar el cambio: " + "; ".join(notes)[:300]
    return tl, st2, msg, notes
