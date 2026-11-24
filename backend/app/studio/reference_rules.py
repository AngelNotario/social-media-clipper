"""La referencia manda desde el inicio.

Antes, el primer corte (reglas) y el director decidían zooms, memes, transiciones y subtítulos a ciegas, y el juez
de estilo llegaba al final con un solo control (los recursos de internet): se quedaba en 26–33 % sin poder subir.
Ahora las metas medidas en tus videos de referencia (style_judge.targets_for) se aplican:

1. before(): al estilo y al ritmo ANTES del primer corte (zoom alterno, transiciones, subtítulos, memes, sonidos).
2. after_director(): como tope DESPUÉS del director (golpes de zoom, sacudidas y memes al ritmo de la referencia),
   quedándose con los más fuertes y bien repartidos, en vez de agregar de más y borrar al azar.
3. card_plan(): cómo y cuánto tiempo poner los recursos (tarjeta o ventana pequeña, duración) según la referencia.
"""
from __future__ import annotations


def _f(targets: dict | None, key: str) -> float | None:
    v = (targets or {}).get(key)
    return float(v) if isinstance(v, (int, float)) else None


def before(st: dict, pace: dict, b: dict, targets: dict | None) -> list[str]:
    """Ajusta estilo y ritmo según la referencia. Devuelve qué cambió (para el reporte)."""
    if not targets:
        return []
    notes = []
    zoom = _f(targets, "zoom_per_min")
    if zoom is not None and zoom < 0.3:
        if float(st.get("zoom_amount") or 1.0) > 1.0:
            notes.append("sin zoom alterno entre tomas (tu referencia no usa zooms)")
        st["zoom_amount"] = 1.0
    elif zoom is not None and st.get("zoom_kind") in ("punch", "ramp"):
        st["zoom_per_min"] = zoom  # se colocan en momentos de énfasis (place_zooms), no alternados
        notes.append(f"zooms en los momentos de énfasis ({zoom:g}/min, como tu referencia)")
    elif zoom is not None:
        # zoom alterno más espaciado si la referencia hace pocos zooms por minuto
        st["zoom_every"] = 1 if zoom >= 6 else 2 if zoom >= 3 else 3
    vis = _f(targets, "visual_changes_per_min")
    if vis is not None and vis < 8 and st.get("transition_style") != "cortes":
        st["transition_style"] = "cortes"
        notes.append(f"cortes secos sin transiciones (tu referencia cambia {vis:g} veces por minuto)")
    caps = _f(targets, "captions_ratio")
    if caps is not None and b.get("captions") == "auto":
        on = caps >= 0.3
        if on != bool(pace.get("captions_on")):
            notes.append(("subtítulos encendidos" if on else "subtítulos apagados") +
                         f" (tu referencia los tiene el {round(caps * 100)} % del tiempo)")
        pace["captions_on"] = on
    memes = _f(targets, "memes_ratio")
    if memes is not None and memes < 0.01 and st.get("memes", True):
        st["memes"] = False
        notes.append("sin textos meme (tu referencia no los usa)")
    txt = _f(targets, "text_per_min")
    if txt is not None:  # densidad de textos destacados como la de tu referencia
        st["callouts_per_min"] = int(max(0, min(12, round(txt))))
    sfx = _f(targets, "sfx_per_min")
    if sfx is not None:
        st["sfx_per_min"] = sfx
        st["sfx_density"] = max(0.0, min(0.5, sfx / 10))
    if targets.get("music") == "no" and b.get("music") == "auto":
        st["music_volume"] = 0.0
        notes.append("sin música de fondo (tu referencia no tiene)")
    return notes


def _spread(items: list, keep: int, key) -> list:
    """Los `keep` más fuertes, repartidos: se divide el tiempo en tramos iguales y en cada uno gana el más fuerte."""
    if keep <= 0:
        return []
    if len(items) <= keep:
        return items
    items = sorted(items, key=lambda x: x[0])
    t0, t1 = items[0][0], items[-1][0] + 1e-6
    size = (t1 - t0) / keep
    out = []
    for i in range(keep):
        a, z = t0 + i * size, t0 + (i + 1) * size
        grp = [x for x in items if a <= x[0] < z]
        if grp:
            out.append(max(grp, key=key))
    return out


def after_director(tl: dict, targets: dict | None) -> dict:
    """Topes de la referencia sobre lo que puso el director. Devuelve cuántos quitó de cada tipo."""
    if not targets:
        return {}
    mins = max(float(tl.get("duration") or 0) / 60, 0.1)
    segs = tl["tracks"]["video"]
    removed = {}
    zoom = _f(targets, "zoom_per_min")
    if zoom is not None:
        fx = [(float(s["start"]) + float(f.get("t", 0)), float(f.get("amount", 0.08)), s, f)
              for s in segs for f in (s.get("fx") or []) if f.get("type") in ("punch", "zoom", "shake")]
        alt = sum(1 for i, s in enumerate(segs) if i and abs(float(s.get("zoom", 1) or 1) - float(segs[i - 1].get("zoom", 1) or 1)) > 0.02)
        allow = max(0, int(round(zoom * mins)) - alt)
        keep = {id(x[3]) for x in _spread(fx, allow, key=lambda x: x[1])}
        n = 0
        for s in segs:
            before_n = len(s.get("fx") or [])
            s["fx"] = [f for f in (s.get("fx") or []) if f.get("type") not in ("punch", "zoom", "shake") or id(f) in keep]
            n += before_n - len(s["fx"])
        if n:
            removed["zooms"] = n
    memes = _f(targets, "memes_ratio")
    if memes is not None:
        texts = tl["tracks"]["text"]
        mm = [(float(t["start"]), float(t.get("end", t["start"])) - float(t["start"]), t) for t in texts if t.get("kind") == "meme"]
        budget = memes * float(tl.get("duration") or 0)
        mm_sorted = sorted(mm, key=lambda x: x[0])
        avg = (sum(x[1] for x in mm) / len(mm)) if mm else 1.2
        allow = int(budget / max(avg, 0.5))
        keep = {id(x[2]) for x in _spread(mm_sorted, allow, key=lambda x: x[1])}
        n = len(mm) - len(keep)
        if n > 0:
            tl["tracks"]["text"] = [t for t in texts if t.get("kind") != "meme" or id(t) in keep]
            removed["memes"] = n
    return removed


EMPH_PUNCT = ("!", "?", "¡", "¿")


def _emphasis(tl: dict, curve: list | None) -> list[tuple[float, float, float]]:
    """Momentos de énfasis del propio video: (t, fuerza, fin de la frase). Palabra con exclamación/pregunta,
    palabra larga al cierre de frase o pico de energía de la voz."""
    words = tl.get("words") or []
    if not words:
        return []
    energy = {}
    if curve:
        for t, e in curve:
            energy[round(float(t) * 10)] = float(e)
    def en(t):
        return max([energy.get(round(t * 10) + d, 0.0) for d in (-1, 0, 1, 2)] or [0.0])
    hi = sorted(energy.values())[int(len(energy) * 0.8)] if len(energy) >= 5 else 1.0
    out = []
    for i, w in enumerate(words):
        txt = str(w.get("word") or "")
        # fin de la frase: siguiente palabra con punto o pausa ≥ 0.35 s
        end = float(w["end"])
        for j in range(i, min(i + 12, len(words))):
            end = float(words[j]["end"])
            nxt = words[j + 1] if j + 1 < len(words) else None
            if txt and (str(words[j]["word"]).rstrip().endswith((".", "!", "?", "…")) or
                        nxt is None or float(nxt["start"]) - end >= 0.35):
                break
        score = en(float(w["start"]))
        if any(c in txt for c in EMPH_PUNCT):
            score += 0.6
        if len(txt.strip(".,!?¡¿…")) >= 7:
            score += 0.15
        if score >= hi or any(c in txt for c in EMPH_PUNCT):
            out.append((float(w["start"]), score, end))
    return out


def place_zooms(tl: dict, st: dict, curve: list | None = None) -> int:
    """Zooms medidos en tu referencia (ritmo, intensidad y duración) puestos en los momentos de énfasis de
    TU video: entra en la palabra fuerte y se sostiene hasta el final de la frase. Devuelve cuántos puso."""
    if st.get("zoom_kind") not in ("punch", "ramp"):
        return 0
    rate = float(st.get("zoom_per_min") or 0)
    dur = float(tl.get("duration") or 0)
    if rate <= 0 or dur <= 0:
        return 0
    amount = max(0.02, min(0.5, float(st.get("zoom_amount") or 1.12) - 1.0))
    hold_ref = max(0.4, float(st.get("zoom_hold_s") or 1.5))
    segs = [s for s in tl["tracks"]["video"] if not s.get("rect")]
    existing = [float(s["start"]) + float(f.get("t", 0)) for s in tl["tracks"]["video"]
                for f in (s.get("fx") or []) if f.get("type") in ("punch", "zoom")]
    want = int(round(rate * dur / 60)) - len(existing)
    if want <= 0:
        return 0
    gap = max(2.5, 0.5 * 60.0 / rate)
    cands = sorted(_emphasis(tl, curve), key=lambda x: -x[1])
    taken = list(existing)
    n = 0
    for t, _, end in cands:
        if n >= want:
            break
        if any(abs(t - x) < gap for x in taken):
            continue
        seg = next((s for s in segs if float(s["start"]) <= t < float(s["start"]) + float(s["src_out"]) - float(s["src_in"])), None)
        if not seg:
            continue
        local = t - float(seg["start"])
        room = float(seg["src_out"]) - float(seg["src_in"]) - local - 0.3
        hold = max(0.4, min(end - t, hold_ref * 1.5, room))
        if room < 0.6:
            continue
        fx = {"t": round(max(0.0, local - 0.05), 3), "type": "punch", "amount": round(amount, 3),
              "hold": round(hold, 2), "ref": True, "hold_fixed": True}
        if st.get("zoom_kind") == "ramp":
            fx["rise"] = round(min(1.2, max(0.4, hold * 0.5)), 2)
        seg.setdefault("fx", []).append(fx)
        taken.append(t)
        n += 1
    return n


def card_plan(targets: dict | None, zone: str) -> dict:
    """Cómo presentar los recursos según la referencia: modo, duración máxima, hueco mínimo y tamaño."""
    base = {"mode": "card", "max_len": 6.0 if zone == "sides" else 3.5, "gap": 2.0 if zone == "sides" else 1.2,
            "scale": None}
    if not targets:
        return base
    pip, card = _f(targets, "pip_ratio"), _f(targets, "card_ratio")
    cover = _f(targets, "overlay_ratio")
    if pip is not None and pip > (card or 0) and zone == "sides":
        base.update(mode="pip", scale=0.32)
    if cover is not None and cover >= 0.5 and zone == "sides":
        # la referencia casi siempre tiene un recurso en pantalla: tramos largos y casi seguidos
        base.update(max_len=9.0, gap=0.6)
    elif cover is not None and cover >= 0.25 and zone == "sides":
        base.update(max_len=7.5, gap=1.2)
    return base
