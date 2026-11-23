"""Director de edición: convierte un corte "plano" en una edición con ritmo de video viral.

Capas (de menos a más criterio):
1. jump_cut_ranges: quita tiempos muertos (silencio + poca acción) dentro de cada tramo elegido.
2. stylize: aplica el estilo (aprendido o el viral): zooms alternos, empuje lento en planos largos,
   transiciones solo entre escenas (los jump cuts son secos) y whoosh según densidad.
3. direct: la IA lee lo que se dice + los picos de emoción y decide golpes de zoom, sacudidas, textos meme,
   callouts, efectos de sonido y zooms a la cámara del streamer. Lee la guía de edición, el análisis del
   estilo aprendido y tus opiniones anteriores (knowledge.prompt_context). Sin IA usa reglas por picos.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Callable

import numpy as np

from .. import db
from ..ai import llm
from ..ai.audio import ENERGY_HOP
from . import assets
from . import timeline as tlm

# ---------------- 1. jump cuts en tramos largos ----------------


def jump_cut_ranges(a: float, b: float, words: list[dict], energy: list[float], gap_thr: float,
                    low_energy: float, pad: float = 0.25, min_len: float = 0.7) -> list[tuple[float, float]]:
    """Rangos a conservar dentro de [a, b]: se cortan huecos sin voz más largos que gap_thr y con poca energía
    (menús, cargas, esperas). Los huecos sin voz pero con acción (disparos, explosiones) se quedan."""
    ws = sorted((w for w in words if w["end"] > a and w["start"] < b), key=lambda w: w["start"])
    gaps, cur = [], a
    for w in ws:
        if w["start"] > cur:
            gaps.append((cur, w["start"]))
        cur = max(cur, w["end"])
    if b > cur:
        gaps.append((cur, b))
    cuts = []
    for g0, g1 in gaps:
        if g1 - g0 < gap_thr:
            continue
        i0, i1 = int(g0 / ENERGY_HOP), max(int(g1 / ENERGY_HOP), int(g0 / ENERGY_HOP) + 1)
        seg_e = energy[i0:i1]
        if seg_e and float(np.mean(seg_e)) > low_energy:
            continue
        c0, c1 = (g0 + pad if g0 > a else g0), (g1 - pad if g1 < b else g1)
        if c1 - c0 >= 0.4:
            cuts.append((c0, c1))
    keep, cur = [], a
    for c0, c1 in cuts:
        if c0 - cur >= min_len:
            keep.append((round(cur, 3), round(c0, 3)))
        cur = c1
    if b - cur >= min_len:
        keep.append((round(cur, 3), round(b, 3)))
    return keep or [(a, b)]


SOUND_ROLES = {  # papel del sonido → etiquetas que lo identifican en tu biblioteca
    "impacto": ("impacto", "boom", "golpe"),
    "revelacion": ("bassdrop", "revelacion", "drop"),
    "fallo": ("error", "fallo", "incorrecto"),
    "suspenso": ("riser", "suspenso", "tension"),
    "exito": ("ding", "exito", "campana"),
    "transicion": ("whoosh", "swoosh", "transicion"),
    "texto": ("pop", "burbuja"),
}
ROLE_VOLUME = {"impacto": 0.42, "revelacion": 0.45, "fallo": 0.4, "suspenso": 0.3, "exito": 0.35,
               "transicion": 0.28, "texto": 0.22}
_UI_PACKS = ("interface", "ui", "top", "viral", "songs", "click", "back", "close", "maximize", "minimize",
             "confirmation", "glass", "bong", "drop 0", "select", "toggle", "scroll", "switch")


# ---------------- 2. estilo base ----------------

def stylize(tl: dict, st: dict, seed: str = "") -> None:
    """Zooms alternos, empuje lento y transiciones entre escenas según el estilo."""
    segs = tl["tracks"]["video"]
    zoom_amt, every = st["zoom_amount"], max(1, int(st["zoom_every"]))
    pattern = {"dinamico": ["whip", "zoom", "flash", "glitch"], "suave": ["fade"], "cortes": []}.get(
        st.get("transition_style", "dinamico"), [])
    whoosh = sound_for("transicion")
    k, last_whoosh = 0, -99.0
    for i, s in enumerate(segs):
        ln = s["src_out"] - s["src_in"]
        if not s.get("rect"):
            s["zoom"] = zoom_amt if zoom_amt > 1.0 and (i // every) % 2 == 1 else 1.0
            if ln >= 3.5:  # plano largo: empuje lento (alterna acercar / alejar)
                s["drift"] = 0.07 if (i % 2 == 0) else -0.05
        s["transition"] = "cut"
        if i and s.get("scene_start") and pattern:
            s["transition"] = pattern[k % len(pattern)]
            k += 1
            if whoosh and s["transition"] in ("whip", "zoom") and s["start"] - last_whoosh >= (4 if st.get("sfx_on_cuts") else 10):
                tl["tracks"]["sfx"].append({"id": tlm.nid("s"), "asset_id": whoosh["id"],
                                            "start": max(0.0, s["start"] - 0.12), "volume": ROLE_VOLUME["transicion"]})
                last_whoosh = s["start"]


# ---------------- 3. director con IA ----------------

DIRECTOR_SYSTEM = """Eres el editor de un canal de gaming/streams con videos virales en español (nivel de los mejores
editores de TikTok y YouTube). Recibes un tramo del video YA CORTADO: lo que se dice con su tiempo en el timeline y
los picos de emoción del audio. Decide los efectos. MENOS ES MÁS: un efecto bien puesto vale más que diez al azar.
Tipos de evento:
- "punch": acercamiento (zoom in) que se sostiene durante la frase. Úsalo en el remate de un chiste, una frase
  clave, una reacción fuerte o una jugada importante. Nunca en frases normales ni seguidos (deja 3 s o más).
- "shake": sacudida corta SOLO en un impacto real y muy fuerte (explosión, grito enorme, muerte). Rarísimo.
- "meme": texto meme grande de 1-3 palabras que exagera la emoción ("¡NOOO!", "CLUTCH", "F", "¿QUÉ?", "NI MODO").
  Solo cuando la emoción es clara. Varía, nada genérico, en el tono del creador.
- "callout": frase clave o dato de lo que se dice (máx. 5 palabras), cuando aporte contexto.
- "sfx": efecto de sonido con "sound" según lo que pasa:
  impacto (golpe, kill, caída), revelacion (giro o remate inesperado), fallo (error, fail, algo sale mal),
  suspenso (antes de un momento tenso; suena justo antes), exito (logro, dato bueno), transicion (cambio de tema).
  Solo si el momento lo pide de verdad. NUNCA sonidos en frases normales.
- "cam": zoom a la cámara del streamer para ver su reacción (risa, grito, sorpresa). {cam_rule}
Usa los tiempos exactos del timeline. No pongas dos textos a menos de 3 s ni dos sonidos a menos de 4 s.
Si "sonidos_que_usa_tu_estilo" trae tipos, son los que usan los videos que el creador quiere imitar: prefiérelos.
Responde solo JSON.

{context}"""

DIRECTOR_SCHEMA = {
    "type": "object",
    "properties": {"events": {"type": "array", "items": {"type": "object", "properties": {
        "t": {"type": "number"}, "type": {"type": "string", "enum": ["punch", "shake", "meme", "callout", "sfx", "cam"]},
        "text": {"type": "string"}, "sound": {"type": "string", "enum": list(SOUND_ROLES)}},
        "required": ["t", "type"]}}},
    "required": ["events"],
}


def timeline_curve(tl: dict, energy: list[float]) -> list[tuple[float, float]]:
    """Curva de energía en tiempo del timeline (muestras cada ENERGY_HOP)."""
    out = []
    for s in tl["tracks"]["video"]:
        i0, i1 = int(s["src_in"] / ENERGY_HOP), int(s["src_out"] / ENERGY_HOP)
        for i in range(i0, max(i1, i0 + 1)):
            if i < len(energy):
                out.append((round(s["start"] + (i - i0) * ENERGY_HOP, 2), float(energy[i])))
    return out


def peaks(curve: list[tuple[float, float]], a: float, b: float, n: int, min_gap: float = 4.0) -> list[tuple[float, int]]:
    pts = [(t, e) for t, e in curve if a <= t < b]
    if not pts:
        return []
    thr = float(np.percentile([e for _, e in curve], 80)) if curve else 0.7
    out: list[tuple[float, int]] = []
    for t, e in sorted(pts, key=lambda x: -x[1]):
        if e < thr or len(out) >= n:
            break
        if all(abs(t - u) >= min_gap for u, _ in out):
            out.append((t, int(e * 100)))
    return sorted(out)


def _editing_sfx() -> list[dict]:
    """Efectos de EDICIÓN (cortos). Fuera los sonidos de interfaz de apps (clics, cerrar, maximizar…) y las
    canciones: suenan raros en un clip y no encajan con lo que pasa."""
    out = []
    for a in assets.list_assets("sfx"):
        tags = f"{a['tags']} {a['title']}".lower()
        if a["available"] and 0 < (a.get("duration") or 0) < 4 and not any(u in tags for u in _UI_PACKS):
            out.append(a)
    return out


def sound_for(role: str, pool: list[dict] | None = None) -> dict | None:
    want = SOUND_ROLES.get(role, ())
    for a in pool if pool is not None else _editing_sfx():
        tags = a["tags"].lower().split()
        if any(w in tags for w in want):
            return a
    return None


def _sfx_catalog() -> list[dict]:
    """Compatibilidad: catálogo de efectos de edición (para otros módulos)."""
    return [{"id": a["id"], "title": a["title"][:40], "tags": " ".join(a["tags"].split()[:5])} for a in _editing_sfx()][:30]


def _seg_at(tl: dict, t: float) -> tuple[int, dict] | tuple[None, None]:
    for i, s in enumerate(tl["tracks"]["video"]):
        if s["start"] <= t < s["start"] + (s["src_out"] - s["src_in"]):
            return i, s
    return None, None


def _split_region(tl: dict, t0: float, t1: float, **fields) -> dict | None:
    """Convierte [t0, t1] del timeline en un segmento propio (sin cambiar la duración total)."""
    i, s = _seg_at(tl, t0)
    if s is None:
        return None
    end = s["start"] + (s["src_out"] - s["src_in"])
    t1 = min(t1, end)
    if t1 - t0 < 0.6:
        return None
    a, b = s["src_in"] + (t0 - s["start"]), s["src_in"] + (t1 - s["start"])
    parts = []
    if a - s["src_in"] >= 0.3:
        parts.append({**s, "id": tlm.nid("v"), "src_out": round(a, 3), "fx": []})
    else:
        a = s["src_in"]
    mid = {**s, "id": tlm.nid("v"), "src_in": round(a, 3), "src_out": round(b, 3), "transition": "cut",
           "fx": [], "drift": 0, "zoom": 1.0, **fields}
    mid.pop("track", None)
    parts.append(mid)
    if s["src_out"] - b >= 0.3:
        parts.append({**s, "id": tlm.nid("v"), "src_in": round(b, 3), "transition": "cut", "fx": [],
                      "scene_start": False})
    else:
        mid["src_out"] = s["src_out"]
    if parts[0] is not mid:
        parts[0]["transition"] = s.get("transition", "cut")
    tl["tracks"]["video"][i:i + 1] = parts
    t = 0.0
    for x in tl["tracks"]["video"]:
        x["start"] = round(t, 3)
        t += x["src_out"] - x["src_in"]
    return mid


def _all_fx_times(tl: dict) -> list[float]:
    return [x["start"] + f["t"] for x in tl["tracks"]["video"] for f in (x.get("fx") or [])]


def thin_fx(tl: dict, gap: float) -> int:
    """Deja como mucho un zoom/efecto de cámara cada `gap` segundos (ritmo de video largo)."""
    if gap <= 0:
        return 0
    removed, last = 0, -1e9
    for s in tl["tracks"]["video"]:
        keep = []
        for f in sorted(s.get("fx") or [], key=lambda x: x["t"]):
            t = s["start"] + f["t"]
            if t - last >= gap:
                keep.append(f)
                last = t
            else:
                removed += 1
        s["fx"] = keep
    return removed


def _add_fx(tl: dict, t: float, kind: str, amount: float, gap: float = 0.8, hold: float | None = None) -> bool:
    """Añade un efecto en el tiempo t del timeline si no choca con otro (separación `gap` en todo el timeline)."""
    _, s = _seg_at(tl, t)
    if s is None or s.get("rect"):
        return False
    if any(abs(u - t) < gap for u in _all_fx_times(tl)):
        return False
    local = round(t - s["start"], 3)
    if s["src_out"] - s["src_in"] - local < 0.35:  # demasiado pegado al corte
        return False
    f = {"t": local, "type": kind, "amount": amount}
    if hold is not None:
        f["hold"] = round(hold, 2)
    s.setdefault("fx", []).append(f)
    s["fx"].sort(key=lambda x: x["t"])
    return True


def finalize_holds(tl: dict) -> None:
    """Cada golpe de zoom se sostiene hasta el final de la frase (como un editor), sin pisar el siguiente
    efecto ni salirse del segmento."""
    from .render import PUNCH_IN, PUNCH_OUT
    words = tl.get("words") or []
    for s in tl["tracks"]["video"]:
        fx = sorted(s.get("fx") or [], key=lambda f: f["t"])
        ln = s["src_out"] - s["src_in"]
        for i, f in enumerate(fx):
            if f["type"] != "punch" or f.get("hold_fixed"):
                continue
            t = s["start"] + f["t"]
            want = 1.2
            for j, w in enumerate(words):  # hasta el final de la frase o de la pausa siguiente
                if w["end"] <= t + 0.3:
                    continue
                nxt = words[j + 1]["start"] if j + 1 < len(words) else 1e9
                if re.search(r"[.!?…,;:]$", w["word"]) or nxt - w["end"] > 0.35:
                    want = w["end"] + 0.15 - t - PUNCH_IN
                    break
            nxt_fx = fx[i + 1]["t"] if i + 1 < len(fx) else 1e9
            room = min(nxt_fx - f["t"] - PUNCH_IN - PUNCH_OUT - 0.1, ln - f["t"] - PUNCH_IN - PUNCH_OUT - 0.05)
            f["hold"] = round(max(0.35, min(2.2, want, room)), 2)


def direct(tl: dict, st: dict, curve: list[tuple[float, float]], voice_curve: list[tuple[float, float]] | None,
           context: str, cam_ok: Callable[[float], dict | None] | None = None,
           progress: Callable[[float, str], None] = lambda p, s: None, window: float = 150.0) -> dict:
    """Añade al timeline los efectos decididos por la IA (o por reglas si no hay IA). Devuelve estadísticas."""
    url, model = db.get_setting("ollama_url"), db.role_model("director")
    ls = llm.status(url)
    ai = ls["available"] and model in ls["models"]
    dur = tl["duration"]
    pool = _editing_sfx()
    roles = {r: sound_for(r, pool) for r in SOUND_ROLES}
    react = voice_curve or curve
    cpm = max(2, int(st.get("callouts_per_min", 4)))
    dens = st.get("fx_density")
    if dens is None:  # densidad de efectos aprendida de tus clips aprobados / con 👍
        try:
            from .. import taste
            dens = taste.profile().get("fx_density", 1.0)
        except Exception:  # noqa: BLE001
            dens = 1.0
    dens = max(0.5, min(1.5, float(dens)))
    events: list[dict] = []

    for n, a in enumerate(np.arange(0, dur, window)):
        b = min(dur, a + window)
        span = b - a
        pk = peaks(curve, a, b, max(3, int(span / 12)))
        rpk = peaks(react, a, b, max(2, int(span / 25)), 6.0)
        dn = dens
        calm = st.get("transition_style") == "cortes" or st.get("memes") is False  # sin memes ni sacudidas
        lim = {"punch": max(1, int(dn * span / 15)), "shake": 0 if calm else (int(dn * span / 60) if span >= 40 else 0),
               "meme": 0 if calm else max(1, int(dn * span / 30)), "callout": max(1, int(cpm * span / 60 / 2)),
               "sfx": (max(1, min(int(span / 4), round(float(st["sfx_per_min"]) * span / 60 * 0.8)))
                       if st.get("sfx_per_min") else max(1, int(dn * span / 14))),
               "cam": max(1, int(span / 35)) if cam_ok else 0}
        got: list[dict] = []
        if ai:
            progress(n * window / max(dur, 1), f"El director está editando ({int(a // 60)}:{int(a % 60):02d})")
            lines = []
            for w0 in range(int(a), int(b), 6):  # una línea cada ~6 s de timeline
                txt = " ".join(w["word"] for w in tl["words"] if w0 <= w["start"] < w0 + 6)
                if txt:
                    lines.append(f"[{w0:.0f}s] {txt}")
            user = {
                "tramo": f"{a:.0f}s a {b:.0f}s del timeline",
                "transcripcion": "\n".join(lines)[:5000] or "(sin voz: gameplay)",
                "picos_de_emocion_audio": [{"t": t, "nivel": e} for t, e in pk],
                "picos_de_reaccion_voz": [{"t": t, "nivel": e} for t, e in rpk],
                "maximo_por_tipo": lim,
                "sonidos_disponibles": [r for r, a in roles.items() if a and r != "texto"],
                "sonidos_que_usa_tu_estilo": [r for r in str(st.get("sound_roles") or "").split(",") if r in roles],
            }
            system = DIRECTOR_SYSTEM.replace("{context}", context).replace(
                "{cam_rule}", "Disponible: úsalo en las reacciones más fuertes." if cam_ok else "NO disponible: no lo uses.")
            try:
                r = llm.chat_json(url, model, system, json.dumps(user, ensure_ascii=False), DIRECTOR_SCHEMA,
                                  num_ctx=12288, max_tokens=1400)
                got = [e for e in r.get("events", []) if isinstance(e, dict)]
            except llm.LLMError:
                got = []
        if not got:  # reglas: acercamientos en los picos más altos y cámara en reacciones (sin sacudidas ni sonidos)
            got = [{"t": t, "type": "punch"} for t, e in pk if e >= 75]
            got += [{"t": t, "type": "cam"} for t, _ in rpk[:lim["cam"]]]
        counts: dict[str, int] = {}
        for e in sorted(got, key=lambda x: float(x.get("t", 0))):
            try:
                t = float(e["t"])
            except (TypeError, ValueError, KeyError):
                continue
            typ = e.get("type")
            if not (a <= t < b) or typ not in lim or counts.get(typ, 0) >= lim[typ]:
                continue
            counts[typ] = counts.get(typ, 0) + 1
            events.append({**e, "t": t})

    stats = {"punch": 0, "shake": 0, "meme": 0, "callout": 0, "sfx": 0, "cam": 0, "ai": ai}
    # Primero los zooms a cámara (dividen segmentos), después lo demás
    last_cam = -99.0
    for e in [x for x in events if x["type"] == "cam"]:
        if e["t"] - last_cam < 20 or not cam_ok:
            continue
        rect = cam_ok(e["t"])
        if rect and _split_region(tl, max(0.0, e["t"] - 0.35), e["t"] + 1.6, rect=rect):
            last_cam = e["t"]
            stats["cam"] += 1
    text_times: list[float] = [t["start"] for t in tl["tracks"]["text"]]
    sfx_times: list[float] = [x["start"] for x in tl["tracks"]["sfx"]]

    def add_sound(role: str, t: float) -> bool:
        a = roles.get(role)
        if not a or any(abs(t - u) < 4.0 for u in sfx_times):
            return False
        at = t - min(1.2, a.get("duration") or 1.2) + 0.1 if role == "suspenso" else t - 0.04  # el riser termina en t
        tl["tracks"]["sfx"].append({"id": tlm.nid("s"), "asset_id": a["id"], "start": round(max(0.0, at), 3),
                                    "volume": ROLE_VOLUME[role]})
        sfx_times.append(t)
        return True

    for e in events:
        t, typ = e["t"], e["type"]
        if typ == "punch":
            if _add_fx(tl, t, "punch", 0.11, gap=2.8):
                stats["punch"] += 1
        elif typ == "shake":
            if _add_fx(tl, t, "shake", 0.16, gap=2.8):
                stats["shake"] += 1
                add_sound("impacto", t)
        elif typ in ("meme", "callout"):
            text = str(e.get("text") or "").strip()
            if not text or any(abs(t - x) < 3 for x in text_times):
                continue
            words = text.split()
            if typ == "meme" and (len(words) > 3 or not st.get("memes", True)):
                continue  # un meme cortado a la mitad ("¿DÓNDE SACAN LOS") se ve fatal: mejor ninguno
            text = " ".join(words[:5])
            if typ == "callout":
                from .agent import callout_is_said
                if not callout_is_said(tl, text, t):
                    continue  # el director no inventa datos: solo resalta lo que se dice
            horiz = tl.get("width", 1080) > tl.get("height", 1920)
            kind = "lower" if (typ == "callout" and horiz) else typ
            if kind == "lower" and text.isupper():
                text = text[:1] + text[1:].lower()  # rótulo en minúsculas, como un subtítulo de noticia
            tl["tracks"]["text"].append({"id": tlm.nid("t"), "start": round(t, 3),
                                         "end": round(min(dur, t + (1.3 if typ == "meme" else (3.0 if horiz else 2.2))), 3),
                                         "text": text[:40], "kind": kind})
            text_times.append(t)
            stats[typ] += 1
            if typ == "meme":
                _add_fx(tl, t, "punch", 0.08, gap=1.5)
                add_sound("texto", t)
        elif typ == "sfx":
            role = e.get("sound") if e.get("sound") in SOUND_ROLES else "impacto"
            if add_sound(role, t):
                stats["sfx"] += 1
    finalize_holds(tl)
    return stats


def music_bed(tl: dict, st: dict) -> bool:
    """Música libre de derechos (carpeta data\\media\\musica) si el estilo usa música."""
    if st.get("music_volume", 0) <= 0:
        return False
    music = [a for a in assets.list_assets("music") if a["available"] and "derechos" not in a["tags"]]
    pick = [a for a in music if any(k in a["tags"] for k in ("electronic", "epic", "energetic", "trap"))] or music
    if not pick:
        return False
    a = pick[int(tl["duration"] * 7) % len(pick)]
    tl["tracks"]["music"].append({"id": tlm.nid("m"), "asset_id": a["id"], "start": 0.0, "end": tl["duration"],
                                  "volume": st["music_volume"]})
    return True


def _nearest(grid: list[float], t: float) -> float | None:
    if not grid:
        return None
    i = int(np.searchsorted(grid, t))
    cands = [grid[j] for j in (i - 1, i) if 0 <= j < len(grid)]
    return min(cands, key=lambda b: abs(b - t))


def snap_to_beats(tl: dict, grid: list[float], strong: list[float] | None = None, tol: float = 0.18,
                  accent_gap: float = 6.0) -> dict:
    """Mueve zooms, sacudidas, textos y efectos al beat más cercano (si está a menos de `tol` s) y,
    en tramos largos sin efectos, añade un golpe de zoom suave en un beat fuerte (edición "al ritmo")."""
    stats = {"snapped": 0, "accents": 0}
    grid = sorted(grid)
    if not grid:
        return stats
    for s in tl["tracks"]["video"]:
        ln = s["src_out"] - s["src_in"]
        for f in s.get("fx") or []:
            b = _nearest(grid, s["start"] + f["t"])
            if b is not None and abs(b - s["start"] - f["t"]) <= tol and 0.05 <= b - s["start"] <= ln - 0.05:
                f["t"] = round(b - s["start"], 3)
                stats["snapped"] += 1
    for t in tl["tracks"]["text"]:
        if t.get("kind") not in ("meme", "callout"):
            continue
        b = _nearest(grid, t["start"])
        if b is not None and abs(b - t["start"]) <= tol:
            d = b - t["start"]
            t["start"], t["end"] = round(max(0.0, t["start"] + d), 3), round(t["end"] + d, 3)
            stats["snapped"] += 1
    for x in tl["tracks"]["sfx"]:
        b = _nearest(grid, x["start"])
        if b is not None and abs(b - x["start"]) <= tol:
            x["start"] = round(max(0.0, b - 0.02), 3)
            stats["snapped"] += 1
    # acentos: golpes suaves en beats fuertes donde la edición se queda quieta
    strong = sorted(strong or [])
    marks = sorted(s["start"] + f["t"] for s in tl["tracks"]["video"] for f in (s.get("fx") or []))
    edges = [0.0] + marks + [tl["duration"]]
    for a, b in zip(edges, edges[1:]):
        if b - a < accent_gap:
            continue
        mid = [x for x in strong if a + 2.0 <= x <= b - 2.0]
        if mid and _add_fx(tl, mid[len(mid) // 2], "punch", 0.07, gap=2.5):
            stats["accents"] += 1
    finalize_holds(tl)  # los golpes movidos al beat recalculan cuánto se sostienen
    return stats


def apply_beats(tl: dict) -> dict | None:
    """Sincroniza la edición con la primera canción del timeline (si hay y se puede analizar)."""
    from ..ai import stems
    m = next(iter(tl["tracks"]["music"]), None)
    a = assets.get_asset(m["asset_id"]) if m else None
    if not a or not a.get("available"):
        return None
    info = stems.beats_for(Path(a["path"]))
    if not info:
        return None
    end = m.get("end", tl["duration"])
    r = snap_to_beats(tl, stems.beat_grid(info, m["start"], end), stems.beat_grid(info, m["start"], end, strong=True))
    return {**r, "tempo": round(info["tempo"])}


__all__ = ["jump_cut_ranges", "stylize", "direct", "timeline_curve", "music_bed", "snap_to_beats", "apply_beats", "Path"]
