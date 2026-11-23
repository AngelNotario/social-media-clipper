"""Modelo de timeline del Studio.

{
  "version": 1, "width": 1080, "height": 1920, "fps": 30, "duration": 42.3,
  "tracks": {
    "video": [ {id, src_in, src_out, start, zoom, crop_x} ],      # pista principal (tu toma), en orden
    "broll": [ {id, asset_id, start, end, src_in, mode?, pos?, scale?, rot?, anim?, frame?, kenburns?, label?} ],
                                                                   # video/imagen encima: "full" (pantalla completa,
                                                                   # por defecto) o tarjeta "card"/"pip" (ver broll_opts)
    "text":  [ {id, start, end, text, kind: title|callout|lower} ],
    "music": [ {id, asset_id, start, end, volume} ],
    "sfx":   [ {id, asset_id, start, volume} ]
  },
  "captions": {"enabled": true},
  "words": [ {start, end, word} ]                                 # subtítulos ya en tiempo del timeline
}
La pista "video" es la toma principal (video o solo audio). Las demás son superposiciones en tiempo absoluto.
"""
from __future__ import annotations

import copy
import hashlib
import json
import uuid

TRACKS = ("video", "broll", "text", "music", "sfx")
FORMATS = {"vertical": (1080, 1920), "horizontal": (1920, 1080), "square": (1080, 1080)}
TEXT_KINDS = {"title", "callout", "lower", "meme"}
FX_TYPES = {"punch", "shake"}


CARD_MODES = ("full", "card", "pip")
CARD_ANIMS = ("pop", "slide_left", "slide_right", "slide_up", "fade", "none")
CARD_FRAMES = ("white", "shadow", "none")
BG_ANIMS = ("cut", "fade", "slide")
BG_FITS = ("cover", "contain")


def _num(v, lo: float, hi: float, d: float) -> float:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return d
    return d if x != x else max(lo, min(hi, x))


def _hexcol(v, d: str) -> str:
    v = str(v or "").strip()
    if len(v) == 7 and v.startswith("#"):
        try:
            int(v[1:], 16)
            return v.upper()
        except ValueError:
            pass
    return d


def broll_opts(b: dict, is_image: bool = False) -> dict:
    """Opciones de presentación de un b-roll, normalizadas. Sin "mode" = pantalla completa como siempre."""
    mode = b.get("mode") if b.get("mode") in CARD_MODES else "full"
    pos = b.get("pos") if isinstance(b.get("pos"), dict) else {}
    dx, dy = (0.8, 0.26) if mode == "pip" else (0.68, 0.45)
    explicit = "mode" in b
    return {
        "mode": mode,
        "pos": {"x": _num(pos.get("x"), 0.0, 1.0, dx), "y": _num(pos.get("y"), 0.0, 1.0, dy)},
        "scale": _num(b.get("scale"), 0.1, 1.0, 0.3 if mode == "pip" else 0.42),
        "rot": _num(b.get("rot"), -12.0, 12.0, 0.0),
        "anim": b.get("anim") if b.get("anim") in CARD_ANIMS else ("pop" if mode != "full" else "none"),
        "frame": b.get("frame") if b.get("frame") in CARD_FRAMES else ("white" if mode == "card" else "shadow"),
        "kenburns": bool(b["kenburns"]) if "kenburns" in b else (is_image and explicit),
        "label": str(b.get("label") or "").strip()[:80],
    }


def norm_broll(it: dict) -> dict:
    """Limpia en su lugar los campos de tarjeta (solo los que existen: no agrega nada a b-rolls de antes)."""
    if "mode" not in it:
        return it
    o = broll_opts(it)
    for k in ("mode", "pos", "scale", "rot", "anim", "frame", "label"):
        if k in it or k == "mode":
            it[k] = o[k]
    if "kenburns" in it:
        it["kenburns"] = bool(it["kenburns"])
    return it


def bg_entry(e: dict) -> dict | None:
    """Tramo del fondo del chroma (tl["chroma"]["bg_schedule"]), normalizado; None si no sirve."""
    try:
        a, b = float(e["start"]), float(e["end"])
    except (KeyError, TypeError, ValueError):
        return None
    if b - a < 0.1:
        return None
    out = {k: v for k, v in e.items() if k not in ("asset_id", "video_id", "kind")}  # se conservan extras ("gen"…)
    out |= {"start": round(max(0.0, a), 3), "end": round(b, 3),
           "anim": e.get("anim") if e.get("anim") in BG_ANIMS else "cut",
           "bg_color": _hexcol(e.get("bg_color"), "#101018")}
    if e.get("kind") == "text" and str(e.get("text") or "").strip():
        out.update(kind="text", text=str(e["text"]).strip()[:60], sub=str(e.get("sub") or "").strip()[:80],
                   color=_hexcol(e.get("color"), "#FFFFFF"), bg_color=_hexcol(e.get("bg_color"), "#E8424F"))
        return out
    if e.get("video_id"):
        out.update(kind="media", video_id=int(e["video_id"]))
    elif e.get("asset_id"):
        out.update(kind="media", asset_id=int(e["asset_id"]))
    else:
        return None
    out.update(src_in=round(_num(e.get("src_in"), 0.0, 1e7, 0.0), 3),
               fit=e.get("fit") if e.get("fit") in BG_FITS else "cover")
    if "kenburns" in e:
        out["kenburns"] = bool(e["kenburns"])
    return out


def bg_schedule(raw: list | None, dur: float) -> list[dict]:
    """Tramos ordenados, recortados a la duración y sin superponerse."""
    items = sorted((x for x in (bg_entry(e) for e in raw or [] if isinstance(e, dict)) if x), key=lambda x: x["start"])
    out, last = [], 0.0
    for x in items:
        x["start"] = max(x["start"], last)
        x["end"] = min(x["end"], dur) if dur else x["end"]
        if x["end"] - x["start"] >= 0.1:
            out.append(x)
            last = x["end"]
    return out


def nid(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:6]}"


def empty(fmt: str = "vertical", fps: int = 30) -> dict:
    w, h = FORMATS.get(fmt, FORMATS["vertical"])
    return {"version": 1, "width": w, "height": h, "fps": fps, "duration": 0.0,
            "tracks": {t: [] for t in TRACKS}, "captions": {"enabled": True}, "words": []}


def thash(tl: dict, style: dict) -> str:
    return hashlib.sha1(json.dumps([tl, style], sort_keys=True).encode()).hexdigest()[:16]


def seg_len(seg: dict) -> float:
    return seg["src_out"] - seg["src_in"]


def to_source(tl: dict, t: float) -> tuple[float, dict] | tuple[None, None]:
    """Tiempo del timeline → (tiempo en la toma original, segmento)."""
    for s in tl["tracks"]["video"]:
        a = float(s.get("start", 0))
        if a - 0.001 <= t <= a + seg_len(s) + 0.001:
            return float(s["src_in"]) + (t - a), s
    return None, None


def add_word_fix(tl: dict, t0: float, t1: float, text: str) -> bool:
    """Corrige el subtítulo que va de t0 a t1 (tiempo del timeline). Se guarda en tiempo de la toma original."""
    a, sa = to_source(tl, t0)
    b, sb = to_source(tl, max(t0, t1 - 0.001))
    if a is None:
        return False
    end = b if sb is sa and b is not None else float(sa["src_out"])
    fix = {"src_start": round(a, 3), "src_end": round(max(a + 0.05, end), 3), "text": str(text or "").strip()[:300]}
    tl["word_fixes"] = [f for f in (tl.get("word_fixes") or [])
                        if f["src_end"] <= fix["src_start"] or f["src_start"] >= fix["src_end"]] + [fix]
    new = fix["text"].split()
    step = (t1 - t0) / max(len(new), 1)
    keep = [w for w in tl.get("words") or [] if not (t0 - 0.01 <= (w["start"] + w["end"]) / 2 <= t1 + 0.01)]
    tl["words"] = sorted(keep + [{"start": round(t0 + i * step, 3), "end": round(t0 + (i + 1) * step - 0.01, 3), "word": w}
                                 for i, w in enumerate(new)], key=lambda w: w["start"])
    return True


def _clean_fixes(fixes) -> list[dict]:
    out = []
    for f in fixes or []:
        try:
            a, b = float(f["src_start"]), float(f["src_end"])
        except (KeyError, TypeError, ValueError):
            continue
        if b > a:
            out.append({"src_start": round(a, 3), "src_end": round(b, 3), "text": str(f.get("text") or "")[:300]})
    return out[-800:]


def apply_word_fixes(words: list[dict], fixes: list[dict]) -> list[dict]:
    """Correcciones de subtítulos hechas en el editor (en tiempo de la toma original, así sobreviven a recortes y
    re-ediciones): las palabras de ese tramo se reemplazan por el texto corregido, repartido en el mismo tiempo.
    Texto vacío = ese subtítulo se quita."""
    if not fixes:
        return words
    out = list(words)
    for f in fixes:
        a, b = f["src_start"], f["src_end"]
        out = [w for w in out if not (a - 0.01 <= (w["start"] + w["end"]) / 2 <= b + 0.01)]
        new = f["text"].split()
        step = (b - a) / max(len(new), 1)
        out += [{"start": round(a + i * step, 3), "end": round(a + (i + 1) * step - 0.01, 3), "word": t}
                for i, t in enumerate(new)]
    return sorted(out, key=lambda w: w["start"])


def relayout(tl: dict, source_words: list[dict], source_duration: float) -> dict:
    """Recalcula inicios de la pista principal, duración total, subtítulos y recorta superposiciones."""
    t = 0.0
    segs = []
    for s in tl["tracks"]["video"]:
        s["src_in"] = round(max(0.0, min(float(s["src_in"]), source_duration)), 3)
        s["src_out"] = round(max(s["src_in"], min(float(s["src_out"]), source_duration)), 3)
        if seg_len(s) < 0.1:
            continue
        s["start"] = round(t, 3)
        s["zoom"] = round(max(1.0, min(float(s.get("zoom", 1.0)), 1.6)), 3)
        s["crop_x"] = round(max(0.0, min(float(s.get("crop_x", 0.5)), 1.0)), 3)
        if "pos" in s and isinstance(s["pos"], dict):
            s["pos"] = {"x": round(max(0.0, min(float(s["pos"].get("x", 0.5)), 1.0)), 3),
                        "y": round(max(0.0, min(float(s["pos"].get("y", 0.5)), 1.0)), 3)}
        else:
            s["pos"] = {"x": 0.5, "y": 0.5}
        if "crop_y" in s:
            s["crop_y"] = round(max(0.0, min(float(s["crop_y"]), 1.0)), 3)
        if s.get("drift"):
            s["drift"] = round(max(-0.3, min(float(s["drift"]), 0.3)), 3)
        if s.get("fx"):  # efectos dentro del segmento (tiempos locales)
            ln = seg_len(s)
            s["fx"] = [{"t": round(max(0.0, min(float(f["t"]), ln)), 3), "type": f["type"],
                        "amount": round(max(0.02, min(float(f.get("amount", 0.15)), 0.5)), 3),
                        **({"hold": round(max(0.2, min(float(f["hold"]), 4.0)), 2)} if f.get("hold") is not None else {}),
                        **({"hold_fixed": True} if f.get("hold_fixed") else {})}
                       for f in s["fx"] if isinstance(f, dict) and f.get("type") in FX_TYPES][:12]
        if s.get("rect"):  # zoom a una región (p. ej. la cámara del stream)
            r = s["rect"]
            s["rect"] = {k: round(max(0.0, min(float(r.get(k, 0)), 1.0)), 4) for k in ("x", "y", "w", "h")}
            if s["rect"]["w"] < 0.03 or s["rect"]["h"] < 0.03:
                s.pop("rect")
        s.setdefault("id", nid("v"))
        t += seg_len(s)
        segs.append(s)
    tl["tracks"]["video"] = segs
    lay = tl.get("layout")
    if isinstance(lay, dict):  # encuadre del proyecto: recortado | desenfocado | barras | stream (cámara + juego)
        mode = lay.get("mode") if lay.get("mode") in ("crop", "blur", "fit", "split") else "crop"
        clean = {"mode": mode, "game_x": round(max(0.0, min(float(lay.get("game_x", 0.5)), 1.0)), 3)}
        if lay.get("src_ar"):
            clean["src_ar"] = round(max(0.2, min(float(lay["src_ar"]), 5.0)), 4)
        cam = lay.get("cam")
        if isinstance(cam, dict) and all(k in cam for k in ("x", "y", "w", "h")):
            clean["cam"] = {k: round(max(0.0, min(float(cam[k]), 1.0)), 4) for k in ("x", "y", "w", "h")}
        tl["layout"] = clean
    dur = round(t, 3)
    if tl.get("chroma"):
        from .chroma import normalize
        sched = tl["chroma"].get("bg_schedule")  # normalize no conoce los tramos de fondo: se conservan aparte
        tl["chroma"] = normalize(tl["chroma"])
        if sched:
            tl["chroma"]["bg_schedule"] = bg_schedule(sched, dur)
    tl["duration"] = dur

    tl["word_fixes"] = _clean_fixes(tl.get("word_fixes"))
    source_words = apply_word_fixes(source_words, tl["word_fixes"])
    words = []
    for s in segs:
        for w in source_words:
            if w["end"] > s["src_in"] + 0.02 and w["start"] < s["src_out"] - 0.02:
                a = max(w["start"], s["src_in"]) - s["src_in"] + s["start"]
                b = min(w["end"], s["src_out"]) - s["src_in"] + s["start"]
                if b - a > 0.02:
                    words.append({"start": round(a, 3), "end": round(b, 3), "word": w["word"]})
    tl["words"] = words

    for track in ("broll", "text", "music"):
        items = []
        for it in tl["tracks"].get(track, []):
            it.setdefault("id", nid(track[0]))
            it["start"] = round(max(0.0, float(it["start"])), 3)
            it["end"] = round(min(float(it["end"]), dur), 3)
            if track == "broll":
                norm_broll(it)
            if it["end"] - it["start"] >= 0.1:
                items.append(it)
        items.sort(key=lambda x: x["start"])
        tl["tracks"][track] = items
    sfx = []
    for it in tl["tracks"].get("sfx", []):
        it.setdefault("id", nid("s"))
        it["start"] = round(max(0.0, float(it["start"])), 3)
        if it["start"] < dur:
            sfx.append(it)
    sfx.sort(key=lambda x: x["start"])
    tl["tracks"]["sfx"] = sfx
    return tl


def find_item(tl: dict, item_id: str) -> tuple[str, dict] | tuple[None, None]:
    for track in TRACKS:
        for it in tl["tracks"].get(track, []):
            if it.get("id") == item_id:
                return track, it
    return None, None


def _shift_overlays(tl: dict, after: float, delta: float, cut_start: float | None = None) -> None:
    """Desplaza las superposiciones posteriores a `after` (delta negativo = adelantar)."""
    for track in ("broll", "text", "music", "sfx"):
        keep = []
        for it in tl["tracks"].get(track, []):
            end = it.get("end", it["start"])
            if cut_start is not None and it["start"] >= cut_start and end <= after and track != "music":
                continue  # quedaba completamente dentro del tramo eliminado
            if it["start"] >= after:
                it["start"] += delta
                if "end" in it:
                    it["end"] += delta
            elif "end" in it and it["end"] > (cut_start if cut_start is not None else after):
                it["end"] = max(it["start"] + 0.1, it["end"] + delta)
            keep.append(it)
        tl["tracks"][track] = keep


def remove_range(tl: dict, start: float, end: float) -> float:
    """Elimina un tramo de la toma principal (ripple). Devuelve los segundos eliminados."""
    start, end = max(0.0, start), min(end, tl["duration"])
    if end - start < 0.05:
        return 0.0
    new = []
    for s in tl["tracks"]["video"]:
        s_start, s_end = s["start"], s["start"] + seg_len(s)
        if s_end <= start or s_start >= end:
            new.append(s)
            continue
        if s_start < start:  # parte anterior
            a = dict(s, id=nid("v"))
            a["src_out"] = s["src_in"] + (start - s_start)
            new.append(a)
        if s_end > end:  # parte posterior
            b = dict(s, id=nid("v"))
            b["src_in"] = s["src_in"] + (end - s_start)
            new.append(b)
    tl["tracks"]["video"] = new
    removed = end - start
    _shift_overlays(tl, end, -removed, cut_start=start)
    return removed


def split_at(tl: dict, t: float) -> bool:
    for i, s in enumerate(tl["tracks"]["video"]):
        a, b = s["start"], s["start"] + seg_len(s)
        if a + 0.15 < t < b - 0.15:
            cut = s["src_in"] + (t - a)
            first = dict(s, src_out=cut)
            second = dict(s, src_in=cut, id=nid("v"))
            tl["tracks"]["video"][i:i + 1] = [first, second]
            return True
    return False


def summary_for_llm(tl: dict, assets_by_id: dict[int, dict]) -> str:
    lines = [f"Duración total: {tl['duration']:.1f}s. Formato {tl['width']}x{tl['height']}. "
             f"Subtítulos: {'activados' if tl['captions'].get('enabled') else 'desactivados'}."]
    lines.append("PISTA VIDEO (toma principal, segmentos en orden):")
    for s in tl["tracks"]["video"]:
        text = " ".join(w["word"] for w in tl["words"] if s["start"] <= w["start"] < s["start"] + seg_len(s))
        lines.append(f"  {s['id']} [{s['start']:.1f}-{s['start'] + seg_len(s):.1f}] zoom={s['zoom']} «{text[:90]}»")
    ch = tl.get("chroma") or {}
    if ch.get("enabled"):
        bg = ch.get("bg") or {}
        lines.append(f"PANTALLA VERDE: activada (color {ch.get('color')}, fondo {bg.get('kind')} "
                     f"video_id={bg.get('video_id')} asset_id={bg.get('asset_id')} desde {bg.get('start', 0)}s, "
                     f"persona escala {ch.get('subject', {}).get('scale', 1)}).")
        if ch.get("bg_schedule"):
            lines.append(f"  FONDO POR TRAMOS: {len(ch['bg_schedule'])} tramos (imágenes, clips y tarjetas de texto).")
    for track, label in (("broll", "B-ROLL"), ("text", "TEXTOS"), ("music", "MÚSICA"), ("sfx", "EFECTOS")):
        items = tl["tracks"][track]
        lines.append(f"PISTA {label}: " + ("(vacía)" if not items else ""))
        for it in items:
            name = assets_by_id.get(it.get("asset_id"), {}).get("title", "")
            if track == "text":
                lines.append(f"  {it['id']} [{it['start']:.1f}-{it['end']:.1f}] {it['kind']}: «{it['text']}»")
            elif track == "sfx":
                lines.append(f"  {it['id']} [{it['start']:.1f}] {name} vol={it.get('volume', 0.6)}")
            else:
                lines.append(f"  {it['id']} [{it['start']:.1f}-{it['end']:.1f}] {name}"
                             + (f" vol={it.get('volume')}" if "volume" in it else ""))
    return "\n".join(lines)


def deepcopy(tl: dict) -> dict:
    return copy.deepcopy(tl)
