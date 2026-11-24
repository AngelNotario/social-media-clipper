"""Juez de estilo: compara la edición con tus videos de referencia (o con un preset del tipo de video) y dice
qué tan parecida es en ESTILO (no en contenido): cuántos recursos pone, de qué tipo, textos, zooms, cortes,
efectos de sonido y música. Da una nota de 0 a 100 y consejos concretos para acercarse (meta: 80 %).

También guarda los mejores ajustes encontrados por estilo (style_tuning) para que la siguiente edición empiece
desde ahí: el editor aprende con cada video.
"""
from __future__ import annotations

import base64
import io
import json
import math
from pathlib import Path

from .. import db

TUNING_SQL = """
CREATE TABLE IF NOT EXISTS style_tuning (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    style_id    INTEGER NOT NULL DEFAULT 0,
    edit_style  TEXT NOT NULL DEFAULT '',
    params      TEXT NOT NULL,
    score       REAL,
    runs        INTEGER NOT NULL DEFAULT 1,
    updated_at  TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    UNIQUE(style_id, edit_style)
);
"""

# clave → (etiqueta, tipo, peso, parámetro que la mueve)
DIMS = {
    "visual_changes_per_min": ("Cambios visuales por minuto (cortes, b-roll, tarjetas)", "rate", 3.0, "broll_per_min"),
    "overlays_per_min": ("Recursos (imágenes/clips) por minuto", "rate", 2.5, "broll_per_min"),
    "card_ratio": ("Recursos en tarjeta", "ratio", 2.0, "card_style"),
    "overlay_ratio": ("Tiempo con recursos en pantalla", "ratio", 1.5, "broll_duration"),
    "text_per_min": ("Rótulos y títulos por minuto", "rate", 1.5, "callouts_per_min"),
    "zoom_per_min": ("Zooms por minuto", "rate", 1.0, "zoom_every"),
    "sfx_per_min": ("Efectos de sonido por minuto", "rate", 1.0, "sfx_per_min"),
    "sfx_on_cuts_ratio": ("SFX en el ritmo (sonido sincronizado con cortes)", "ratio", 1.0, "sfx_on_cuts"),
    "music": ("Música de fondo", "cat", 0.75, "music_volume"),
    "fullscreen_ratio": ("Recursos a pantalla completa", "ratio", 0.75, "card_style"),
    "pip_ratio": ("Recursos en ventana pequeña", "ratio", 0.25, "card_style"),
    "speaker_visible_ratio": ("Tiempo en que se te ve", "ratio", 0.2, "card_style"),
    "captions_ratio": ("Subtítulos", "ratio", 0.5, "captions"),
    "memes_ratio": ("Memes", "ratio", 0.5, "memes"),
}
MUSIC_ORDER = ("no", "tenue", "sí")
EPS = 0.1


def _f(v) -> float | None:
    return float(v) if isinstance(v, (int, float)) and math.isfinite(float(v)) else None


# ---------------- objetivos ----------------

def reference_targets(style_id: int | None) -> dict | None:
    """Objetivos medidos en tus videos de referencia (estilo aprendido). Lo que no se midió queda en None."""
    if not style_id:
        return None
    try:
        with db.get_conn() as conn:
            r = conn.execute("SELECT name, profile FROM learned_styles WHERE id = ?", (style_id,)).fetchone()
    except Exception:  # noqa: BLE001 — la tabla puede no existir todavía
        return None
    if not r:
        return None
    try:
        prof = json.loads(r["profile"] or "{}")
    except ValueError:
        return None
    el, e = prof.get("elements") or {}, prof.get("edit") or {}
    snd, sp, d, av = prof.get("sound") or {}, prof.get("sound_pro") or {}, prof.get("design") or {}, prof.get("audio_visual") or {}
    secs = _f(prof.get("analyzed_seconds")) or _f(prof.get("duration")) or 60.0
    sfx = _f(sp.get("sfx_per_min")) if not sp.get("error") else None
    if sfx is None and _f(snd.get("sfx_estimate")) is not None:
        sfx = round(float(snd["sfx_estimate"]) / max(secs, 1) * 60, 2)
    cuts = _f(e.get("cuts_per_min"))
    if cuts is None and _f(e.get("cuts_per_10s")) is not None:
        cuts = round(float(e["cuts_per_10s"]) * 6, 2)
    music = snd.get("music_bed") if snd.get("music_bed") in MUSIC_ORDER else None
    if music is None and _f(sp.get("music_ratio")) is not None:
        mr = float(sp["music_ratio"])
        music = "no" if mr < 0.15 else ("tenue" if mr < 0.5 else "sí")
    zoom = _f(el.get("zoom_per_min"))
    zm = prof.get("zoom") or {}
    if (_f(zm.get("confidence")) or 0) >= 0.4:  # zooms medidos (cámara) mandan sobre la pista de la IA de visión
        zoom = _f(zm.get("zoom_per_min"))
    ovm = _f(el.get("overlays_per_min"))
    vis = max([v for v in (cuts, ovm) if v is not None], default=None)  # la detección de escenas ya cuenta el b-roll
    # sfx_on_cuts_ratio: si el estilo aprendido sincroniza sonidos con cortes, el ratio esperado es alto
    sfx_on_cuts = None
    if av.get("score") is not None:
        sfx_on_cuts = round(min(1.0, max(0.0, float(av["score"]) / 100.0)), 2)
    elif sp.get("cut_sync") is not None:
        sfx_on_cuts = round(min(1.0, max(0.0, float(sp["cut_sync"]))), 2)
    return {"name": r["name"], "visual_changes_per_min": vis,
            "overlays_per_min": _f(el.get("overlays_per_min")), "card_ratio": _f(el.get("card_ratio")),
            "overlay_ratio": _f(el.get("overlay_ratio")), "fullscreen_ratio": _f(el.get("fullscreen_ratio")),
            "pip_ratio": _f(el.get("pip_ratio")), "speaker_visible_ratio": _f(el.get("speaker_visible_ratio")),
            "text_per_min": _f(el.get("labels_per_min")), "captions_ratio": _f(d.get("captions_ratio")),
            "cuts_per_min": cuts, "zoom_per_min": zoom, "sfx_per_min": sfx, "sfx_on_cuts_ratio": sfx_on_cuts,
            "music": music, "memes_ratio": _f(el.get("memes_ratio")),
            "zoom_amount": _f(zm.get("amount")) if (_f(zm.get("confidence")) or 0) >= 0.4 else None,
            "zoom_hold_s": _f(zm.get("hold_s")) if (_f(zm.get("confidence")) or 0) >= 0.4 else None}


def preset_targets(video_type: str | None, edit_style: str | None) -> dict:
    """Valores razonables cuando no hay referencia (o le faltan medidas)."""
    es = (edit_style or "").lower()
    if (video_type or "long") == "short":
        calm = es in ("informativo", "tutorial", "podcast")
        return {"name": f"preset short {es or 'general'}", "overlays_per_min": 6.0 if calm else 8.0, "card_ratio": 0.5,
                "overlay_ratio": 0.45, "fullscreen_ratio": 0.4, "pip_ratio": 0.1, "speaker_visible_ratio": 0.7,
                "text_per_min": 3.0 if calm else 4.0, "zoom_per_min": 6.0 if calm else 8.0,
                "cuts_per_min": 14.0 if calm else 20.0, "visual_changes_per_min": 14.0 if calm else 20.0, "sfx_per_min": 3.0 if calm else 6.0,
                "sfx_on_cuts_ratio": 0.3 if calm else 0.6,
                "music": "tenue" if calm else "sí", "captions_ratio": 1.0, "memes_ratio": 0.0 if calm else 0.1}
    if es in ("informativo", "tutorial", "podcast", ""):
        return {"name": f"preset largo {es or 'informativo'}", "overlays_per_min": 3.5, "card_ratio": 0.75,
                "overlay_ratio": 0.35, "fullscreen_ratio": 0.2, "pip_ratio": 0.05, "speaker_visible_ratio": 0.85,
                "text_per_min": 1.2, "zoom_per_min": 2.0, "cuts_per_min": 5.0, "visual_changes_per_min": 5.0, "sfx_per_min": 1.5,
                "sfx_on_cuts_ratio": 0.2,
                "music": "tenue", "captions_ratio": 0.0, "memes_ratio": 0.0}
    return {"name": f"preset largo {es}", "overlays_per_min": 4.5, "card_ratio": 0.6, "overlay_ratio": 0.45,
            "fullscreen_ratio": 0.35, "pip_ratio": 0.05, "speaker_visible_ratio": 0.75, "text_per_min": 1.5,
            "zoom_per_min": 3.0, "cuts_per_min": 7.0, "visual_changes_per_min": 7.0, "sfx_per_min": 3.0, "music": "tenue",
            "sfx_on_cuts_ratio": 0.4, "captions_ratio": 0.2, "memes_ratio": 0.05}


def targets_for(style_id: int | None, video_type: str | None, edit_style: str | None) -> dict:
    """Referencia encima del preset: lo medido manda; lo no medido sale del preset. `sources` dice de dónde viene."""
    pre = preset_targets(video_type, edit_style)
    ref = reference_targets(style_id) or {}
    out, src = {}, {}
    for k in list(DIMS) + ["cuts_per_min"]:
        if ref.get(k) is not None:
            out[k], src[k] = ref[k], "referencia"
        else:
            out[k], src[k] = pre.get(k), "preset"
    out["name"] = ref.get("name") or pre["name"]
    out["sources"] = src
    return out


# ---------------- medición de la edición ----------------

def _union(spans: list[tuple[float, float]]) -> float:
    tot, cur = 0.0, None
    for a, b in sorted(spans):
        if cur is None or a > cur[1]:
            if cur:
                tot += cur[1] - cur[0]
            cur = [a, b]
        else:
            cur[1] = max(cur[1], b)
    if cur:
        tot += cur[1] - cur[0]
    return tot


def _caption_cover(tl: dict, dur: float) -> float:
    """Fracción del video con subtítulo en pantalla: solo aparecen mientras hablas (antes contaba 100 %)."""
    words = tl.get("words") or []
    if not words:
        return 1.0
    spans = sorted((float(w["start"]), float(w["end"]) + 0.25) for w in words)  # cada frase se sostiene un momento
    return round(min(1.0, _union(spans) / dur), 2)


def _sfx_on_cuts_ratio(segs: list[dict], sfx: list[dict], tol: float = 0.6) -> float:
    """Proporción de SFX que caen dentro de ±tol segundos de un corte (inicio de segmento de video)."""
    if not sfx:
        return 0.0
    cut_times = [float(s.get("start", 0)) for s in segs[1:]]  # el primer segmento no tiene corte de entrada
    if not cut_times:
        return 0.0
    on_cut = 0
    for s in sfx:
        t = float(s.get("start", 0))
        if any(abs(t - ct) <= tol for ct in cut_times):
            on_cut += 1
    return round(on_cut / len(sfx), 2)


def edit_metrics(tl: dict) -> dict:
    """Las mismas medidas, calculadas sobre un timeline."""
    dur = max(float(tl.get("duration") or 0), 1.0)
    mins = dur / 60
    tr = tl.get("tracks") or {}
    segs = tr.get("video") or []
    broll = tr.get("broll") or []
    texts = tr.get("text") or []
    spans = {"card": 0.0, "full": 0.0, "pip": 0.0}
    all_spans = []
    for b in broll:
        a, e = float(b.get("start", 0)), float(b.get("end", b.get("start", 0)))
        if e <= a:
            continue
        mode = b.get("mode") if b.get("mode") in spans else "full"
        spans[mode] += e - a
        all_spans.append((a, e))
    ck = tl.get("chroma") or {}
    sched = [b for b in (ck.get("bg_schedule") or []) if ck.get("enabled")] if isinstance(ck, dict) else []
    bg_text = 0
    for b in sched:  # fondo del chroma por tramos = recursos a pantalla completa (tu cámara queda en la esquina)
        a, e = float(b.get("start", 0)), float(b.get("end", b.get("start", 0)))
        if e <= a:
            continue
        spans["full"] += e - a
        all_spans.append((a, e))
        bg_text += 1 if b.get("kind") == "text" else 0
    btot = sum(spans.values())
    zooms, last_z = 0, 1.0
    for i, s in enumerate(segs):
        zooms += sum(1 for f in s.get("fx") or [] if f.get("type") in ("punch", "zoom"))
        z = float(s.get("zoom", 1.0) or 1.0)
        if i and abs(z - last_z) > 0.02:
            zooms += 1
        last_z = z
    trans = sum(1 for s in segs[1:] if s.get("transition", "cut") not in ("cut", None))
    full_n = sum(1 for b in broll if (b.get("mode") or "full") == "full")
    other_n = len(broll) - full_n
    changes = full_n * 2 + other_n + len(sched) + zooms + trans
    bg_full = sum(max(0.0, float(b.get("end", 0)) - float(b.get("start", 0))) for b in sched)
    music = tr.get("music") or []
    if not music:
        mus = "no"
    else:
        vol = max(float(m.get("volume", 0.1) or 0) for m in music)
        mus = "no" if vol <= 0.005 else ("tenue" if vol < 0.12 else "sí")
    memes = sum(max(0.0, float(t.get("end", 0)) - float(t.get("start", 0))) for t in texts if t.get("kind") == "meme")
    caps = tl.get("captions") or {}
    return {
        "visual_changes_per_min": round(changes / mins, 2),
        "overlays_per_min": round((len(broll) + len(sched)) / mins, 2),
        "card_ratio": round(spans["card"] / btot, 2) if btot else 0.0,
        "overlay_ratio": round(_union(all_spans) / dur, 2),
        "fullscreen_ratio": round(spans["full"] / btot, 2) if btot else 0.0,
        "pip_ratio": round(spans["pip"] / btot, 2) if btot else 0.0,
        "speaker_visible_ratio": round(max(0.0, 1 - (spans["full"] - bg_full) / dur), 2),
        "speaker_corner": bool(sched) and bg_full / dur > 0.5,  # tu cámara en la esquina sobre el b-roll
        "text_per_min": round((sum(1 for t in texts if t.get("kind") in ("title", "lower", "callout")) + bg_text) / mins, 2),
        "captions_ratio": _caption_cover(tl, dur) if caps.get("enabled") else 0.0,
        "cuts_per_min": round(changes / mins, 2),
        "zoom_per_min": round(zooms / mins, 2),
        "sfx_per_min": round(len(tr.get("sfx") or []) / mins, 2),
        "sfx_on_cuts_ratio": _sfx_on_cuts_ratio(segs, tr.get("sfx") or []),
        "music": mus,
        "memes_ratio": round(min(1.0, memes / dur), 2),
    }


# ---------------- comparación ----------------

def _dim_score(kind: str, ref, mine) -> float:
    if kind == "cat":
        if ref == mine:
            return 1.0
        if ref in MUSIC_ORDER and mine in MUSIC_ORDER:
            return 0.5 if abs(MUSIC_ORDER.index(ref) - MUSIC_ORDER.index(mine)) == 1 else 0.0
        return 0.0
    r, m = float(ref), float(mine or 0)
    if kind == "rate":
        return max(0.0, 1 - abs(math.log((m + EPS) / (r + EPS))) / math.log(3))
    return max(0.0, 1 - abs(m - r) * 2)  # proporción: 0.5 de diferencia = 0


def _why(key: str, action: str, ref, mine) -> str:
    label = DIMS[key][0].lower()
    if DIMS[key][1] == "cat":
        return f"{DIMS[key][0]}: tu edición «{mine}», la referencia «{ref}»"
    fmt = (lambda v: f"{float(v):.0%}") if DIMS[key][1] == "ratio" else (lambda v: f"{float(v):g}")
    return f"{'Falta' if action == 'more' else 'Sobra'}: {label} {fmt(mine or 0)} contra {fmt(ref)} de la referencia"


def similarity(targets: dict, metrics: dict) -> dict:
    """Nota 0-100 (media ponderada de las dimensiones con referencia) + detalle + consejos."""
    dims, advice, tot, wsum = [], [], 0.0, 0.0
    for k, (label, kind, w, param) in DIMS.items():
        ref = targets.get(k)
        if ref is None:
            continue
        mine = metrics.get(k)
        sc = _dim_score(kind, ref, mine)
        if k == "speaker_visible_ratio" and metrics.get("speaker_corner"):
            sc = max(sc, 0.8)  # cámara en la esquina sobre b-roll a pantalla completa ≈ referencia sin cámara
        dims.append({"key": k, "label_es": label, "ref": ref, "mine": mine, "score": round(sc * 100)})
        tot += sc * w
        wsum += w
        if sc < 0.85:
            if kind == "cat":
                action = "more" if MUSIC_ORDER.index(ref) > MUSIC_ORDER.index(mine if mine in MUSIC_ORDER else "no") else "less"
                delta = MUSIC_ORDER.index(ref) - MUSIC_ORDER.index(mine if mine in MUSIC_ORDER else "no")
            else:
                delta = round(float(ref) - float(mine or 0), 2)
                action = "more" if delta > 0 else "less"
            advice.append({"key": k, "param": param, "action": action, "delta": delta,
                           "why_es": _why(k, action, ref, mine), "_w": w * (1 - sc)})
    advice.sort(key=lambda a: -a.pop("_w"))
    return {"score": round(100 * tot / wsum, 1) if wsum else 0.0, "dims": dims, "advice": advice}


# ---------------- comparación visual (opcional) ----------------

VISUAL_SCHEMA = {"type": "object", "properties": {"score": {"type": "number"}, "notes": {"type": "string"}},
                 "required": ["score", "notes"]}
VISUAL_PROMPT = ("La imagen tiene dos columnas: IZQUIERDA = fotogramas de un video de REFERENCIA, DERECHA = fotogramas "
                 "de un video EDITADO. Califica de 0 a 100 qué tanto se parece el ESTILO DE EDICIÓN (cantidad de "
                 "gráficos y recursos en pantalla, tipo de tarjetas/recuadros, estilo de textos, composición, ritmo "
                 "que sugieren), NO el tema ni la persona. En 'notes' di en español, en máximo 40 palabras, qué le "
                 "falta a la derecha para parecerse a la izquierda.")


def _grid(ref_frames: list[Path], edit_frames: list[Path], cell=(480, 270)):
    from PIL import Image, ImageDraw
    rows = 3
    W, H = cell[0] * 2 + 12, cell[1] * rows + 40 + 6 * rows
    g = Image.new("RGB", (W, H), (20, 20, 24))
    d = ImageDraw.Draw(g)
    d.text((10, 12), "REFERENCIA", fill=(255, 255, 255))
    d.text((cell[0] + 22, 12), "EDICION", fill=(255, 255, 255))
    for col, frames in enumerate((ref_frames, edit_frames)):
        for r, f in enumerate(frames[:rows]):
            try:
                im = Image.open(f).convert("RGB")
            except Exception:  # noqa: BLE001
                continue
            im.thumbnail(cell)
            x = col * (cell[0] + 12) + (cell[0] - im.width) // 2
            y = 40 + r * (cell[1] + 6) + (cell[1] - im.height) // 2
            g.paste(im, (x, y))
    return g


def visual_compare(ref_frames: list[Path], edit_frames: list[Path]) -> dict | None:
    """Una sola llamada de visión con una cuadrícula 2x3. None si no hay modelo de visión o fotogramas."""
    ref_frames = [Path(p) for p in ref_frames if Path(p).exists()]
    edit_frames = [Path(p) for p in edit_frames if Path(p).exists()]
    if not ref_frames or not edit_frames:
        return None
    from ..ai import llm
    url = db.get_setting("ollama_url")
    vmodel = db.get_setting("vision_model") or "qwen3.6:35b-a3b"
    try:
        st = llm.status(url)
        if not (st["available"] and vmodel in st["models"]):
            return None
        buf = io.BytesIO()
        _grid(ref_frames, edit_frames).save(buf, "JPEG", quality=85)
        r = llm.chat_json(url, vmodel, "Eres un editor de video senior. Responde solo JSON.", VISUAL_PROMPT,
                          VISUAL_SCHEMA, images=[base64.b64encode(buf.getvalue()).decode()],
                          num_ctx=4096, keep_alive="2m", timeout=300, max_tokens=200)
    except Exception:  # noqa: BLE001 — opcional: nunca tumba la edición
        return None
    sc = _f(r.get("score"))
    if sc is None:
        return None
    return {"score": round(max(0.0, min(100.0, sc)), 1), "notes_es": str(r.get("notes") or "")[:300]}


# ---------------- aprendizaje progresivo ----------------

def _tuning_schema() -> None:
    with db.get_conn() as conn:
        conn.executescript(TUNING_SQL)


def save_tuning(style_id: int | None, edit_style: str | None, params: dict, score: float | None) -> dict:
    """Guarda los ajustes si son los mejores vistos para ese estilo (siempre cuenta la corrida)."""
    _tuning_schema()
    sid, es = int(style_id or 0), (edit_style or "")
    with db.get_conn() as conn:
        r = conn.execute("SELECT id, score, runs FROM style_tuning WHERE style_id = ? AND edit_style = ?", (sid, es)).fetchone()
        if not r:
            conn.execute("INSERT INTO style_tuning (style_id, edit_style, params, score) VALUES (?, ?, ?, ?)",
                         (sid, es, json.dumps(params, ensure_ascii=False), score))
        elif score is not None and (r["score"] is None or score >= r["score"]):
            conn.execute("UPDATE style_tuning SET params = ?, score = ?, runs = runs + 1, "
                         "updated_at = datetime('now','localtime') WHERE id = ?",
                         (json.dumps(params, ensure_ascii=False), score, r["id"]))
        else:
            conn.execute("UPDATE style_tuning SET runs = runs + 1, updated_at = datetime('now','localtime') WHERE id = ?",
                         (r["id"],))
    return load_tuning(style_id, edit_style) or {}


def load_tuning(style_id: int | None, edit_style: str | None) -> dict | None:
    _tuning_schema()
    with db.get_conn() as conn:
        r = conn.execute("SELECT params, score, runs, updated_at FROM style_tuning WHERE style_id = ? AND edit_style = ?",
                         (int(style_id or 0), edit_style or "")).fetchone()
    if not r:
        return None
    try:
        params = json.loads(r["params"])
    except ValueError:
        params = {}
    return {"params": params, "score": r["score"], "runs": r["runs"], "updated_at": r["updated_at"]}


# ---------------- desarme frame-accurate (EDL) ----------------

EDL_TOL = 0.5  # segundos de tolerancia para considerar que un evento coincide


def _edl_add(events: list[dict], t: float, etype: str, **kw) -> None:
    ev = {"t": round(float(t), 3), "type": etype}
    ev.update(kw)
    events.append(ev)


def edl_metrics(tl: dict) -> list[dict]:
    """Extrae del timeline los mismos eventos que el desarme de referencia, con timestamp exacto."""
    dur = max(float(tl.get("duration") or 0), 1.0)
    tr = tl.get("tracks") or {}
    segs = tr.get("video") or []
    broll = tr.get("broll") or []
    texts = tr.get("text") or []
    sfx = tr.get("sfx") or []
    music = tr.get("music") or []
    events: list[dict] = []
    for i, s in enumerate(segs):
        a = float(s.get("start", 0))
        src_in = float(s.get("src_in", a)); src_out = float(s.get("src_out", a))
        ln = max(0.0, src_out - src_in)
        if i > 0:
            _edl_add(events, a, "cut")
        if ln >= 0.1:
            _edl_add(events, a, "scene", end=round(a + ln, 3))
        z = float(s.get("zoom", 1.0) or 1.0)
        if z > 1.01:
            _edl_add(events, a, "zoom", amount=round(z, 3))
        trn = s.get("transition")
        if trn and trn != "cut":
            _edl_add(events, a, "transition", kind=trn)
        for f in s.get("fx") or []:
            if f.get("type") in ("punch", "zoom", "shake"):
                _edl_add(events, round(a + float(f.get("t", 0)), 3), f"fx/{f['type']}",
                         amount=float(f.get("amount", 0.15)))
    for b in broll:
        a = float(b.get("start", 0)); e = float(b.get("end", a))
        mode = b.get("mode") or "full"
        if e - a >= 0.1:
            _edl_add(events, a, f"overlay/{mode}", end=round(e, 3), label=b.get("label"))
    for t in texts:
        a = float(t.get("start", 0)); e = float(t.get("end", a))
        if e - a >= 0.1:
            _edl_add(events, a, "caption", end=round(e, 3), text=t.get("text"), kind=t.get("kind"))
    for s in sfx:
        _edl_add(events, float(s.get("start", 0)), "sfx", volume=float(s.get("volume", 0.6)))
    for m in music:
        a = float(m.get("start", 0)); e = float(m.get("end", a))
        if e - a >= 0.1:
            _edl_add(events, a, "music", end=round(e, 3), volume=float(m.get("volume", 0.1)))
    ck = tl.get("chroma") or {}
    if isinstance(ck, dict) and ck.get("enabled"):
        for b in ck.get("bg_schedule") or []:
            a = float(b.get("start", 0)); e = float(b.get("end", a))
            if e - a >= 0.1:
                _edl_add(events, a, "bg", end=round(e, 3), kind=b.get("kind"))
    events.sort(key=lambda ev: (ev["t"], ev["type"]))
    return events


def _match_point(ref_t: float, mine_ts: list[float], tol: float) -> float | None:
    best, bd = None, None
    for m in mine_ts:
        d = abs(m - ref_t)
        if d <= tol and (bd is None or d < bd):
            best, bd = m, d
    return best


def _cover_ratio(ref_spans: list[tuple[float, float]], mine_spans: list[tuple[float, float]], dur: float) -> float:
    if not ref_spans:
        return 1.0
    rtot = _union(ref_spans)
    if rtot <= 0:
        return 1.0
    covered = 0.0
    for a, b in ref_spans:
        for c, d in mine_spans:
            lo, hi = max(a, c), min(b, d)
            if hi > lo:
                covered += hi - lo
    return round(max(0.0, min(1.0, covered / rtot)), 3)


def compare_edl(ref_edl: list[dict], mine_edl: list[dict], dur: float) -> dict:
    """Compara el desarme de referencia contra el timeline actual. Devuelve score 0-100, detalle por tipo y
    correcciones aplicables (qué agregar y en qué segundo) para acercarse exactamente al estilo."""
    if not ref_edl:
        return {"score": 100.0, "by_type": {}, "corrections": [], "note": "sin referencia de desarme"}
    from collections import defaultdict
    ref_by_type: dict[str, list[dict]] = defaultdict(list)
    mine_by_type: dict[str, list[dict]] = defaultdict(list)
    for ev in ref_edl:
        ref_by_type[ev["type"]].append(ev)
    for ev in mine_edl:
        mine_by_type[ev["type"]].append(ev)

    by_type, corrections, tot, wsum = {}, [], 0.0, 0.0
    for etype, revs in ref_by_type.items():
        mes = mine_by_type.get(etype, [])
        w = 1.0
        if etype in ("cut", "scene"):
            w = 3.0
        elif etype.startswith("overlay/"):
            w = 2.0
        elif etype == "caption":
            w = 2.0
        elif etype == "sfx":
            w = 1.5
        elif etype == "music":
            w = 1.0
        elif etype in ("zoom", "transition") or etype.startswith("fx/"):
            w = 1.0
        else:
            w = 0.75
        # punto vs rango
        is_range = etype in ("scene", "caption", "music", "bg") or etype.startswith("overlay/")
        if is_range:
            ref_spans = [(float(e["t"]), float(e.get("end", e["t"]))) for e in revs if e.get("end", e["t"]) > e["t"] + 0.05]
            mine_spans = [(float(e["t"]), float(e.get("end", e["t"]))) for e in mes if e.get("end", e["t"]) > e["t"] + 0.05]
            score = _cover_ratio(ref_spans, mine_spans, dur)
            # eventos de referencia sin cobertura -> corrección add
            for e in revs:
                a, b = float(e["t"]), float(e.get("end", e["t"]))
                if b - a < 0.05:
                    continue
                hit = any(max(a, c) < min(b, d) for c, d in mine_spans)
                if not hit:
                    corrections.append({"op": "add_" + etype.replace("/", "_"), "t": round(a, 3),
                                        "end": round(b, 3), "type": etype, "ref_t": round(a, 3),
                                        "detail": e})
        else:
            ref_ts = [float(e["t"]) for e in revs]
            mine_ts = [float(e["t"]) for e in mes]
            hits = 0
            used = set()
            for rt in ref_ts:
                mt = _match_point(rt, mine_ts, EDL_TOL)
                if mt is not None:
                    hits += 1
                    used.add(mt)
            score = hits / max(len(ref_ts), 1)
            # eventos de referencia sin coincidencia -> corrección add en ese segundo
            for e in revs:
                rt = float(e["t"])
                if _match_point(rt, mine_ts, EDL_TOL) is None:
                    corrections.append({"op": "add_" + etype.replace("/", "_"), "t": round(rt, 3),
                                        "type": etype, "ref_t": round(rt, 3), "detail": e})
        by_type[etype] = {"ref": len(revs), "mine": len(mes), "score": round(score * 100, 1)}
        tot += score * w
        wsum += w
    score = round(100 * tot / wsum, 1) if wsum else 0.0
    return {"score": score, "by_type": by_type, "corrections": corrections}


def edl_similarity(targets: dict, metrics: dict, ref_edl: list[dict], mine_edl: list[dict], dur: float) -> dict:
    """Combina la nota de tasas (similarity) con la nota de desarme frame-accurate (compare_edl)."""
    base = similarity(targets, metrics)
    edl = compare_edl(ref_edl, mine_edl, dur)
    # 60 % tasas, 40 % desarme frame-accurate
    combined = round(0.6 * base["score"] + 0.4 * edl["score"], 1)
    advice = list(base.get("advice", []))
    for c in edl.get("corrections", [])[:12]:
        advice.append({"key": "edl:" + c["type"], "param": c["op"], "action": "more",
                       "delta": 0.0, "why_es": f"Agrega {c['type']} en t={c['t']}s (estilo de referencia).",
                       "_w": 1.0, "_edl_correction": c})
    advice.sort(key=lambda a: -a.pop("_w", 0))
    return {"score": combined, "base_score": base["score"], "edl_score": edl["score"],
            "dims": base.get("dims", []), "edl_by_type": edl.get("by_type", {}),
            "corrections": edl.get("corrections", []), "advice": advice,
            "base_advice": base.get("advice", [])}
