"""Juez de sonido: revisa los efectos de sonido de una edición como lo haría un editor de audio.

Qué revisa (y corrige solo):
- Que cada efecto tenga un motivo visible (un corte con transición, un zoom, un texto, un meme, un b-roll).
  Un efecto sin motivo es "puesto al azar" y se quita.
- Que el tipo de sonido encaje con el estilo: en un video serio/informativo no van memes ni golpes fuertes.
- Densidad y espacio entre efectos, comparando con tus videos de referencia (estilo aprendido) si los hay.
- Volumen real de cada archivo (pico medido), para que ningún efecto tape tu voz.
- Con un render ya hecho, "escucha" el resultado: si un efecto sobresale mucho de tu voz, lo baja.
- Con la IA disponible, un juez lee lo que dices en cada momento y decide si el efecto aporta o distrae.
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

from .. import config, db
from . import assets

MEME_WORDS = ("meme", "boing", "bruh", "vine", "fart", "pedo", "airhorn", "bocina", "risa", "laugh", "cartoon",
              "honk", "scratch", "oof", "wow", "sad", "violin", "troll", "emotional damage", "cricket", "grillo",
              "nope", "fail horn", "trombone", "trombon", "yeet", "anime")
HARD_WORDS = ("boom", "impacto", "impact", "golpe", "hit", "punch", "bassdrop", "drop", "explosion", "thud", "slam")
SOFT_WORDS = ("whoosh", "swoosh", "transicion", "riser", "suspenso", "tension", "ding", "pop", "click", "burbuja")

LEVELS_FILE = "sfx_levels.json"


def category(a: dict | None) -> str:
    if not a:
        return "desconocido"
    t = f"{a.get('tags', '')} {a.get('title', '')}".lower()
    if any(w in t for w in MEME_WORDS):
        return "meme"
    if any(w in t for w in HARD_WORDS):
        return "fuerte"
    if any(w in t for w in SOFT_WORDS):
        return "suave"
    return "otro"


def _levels_cache() -> tuple[Path, dict]:
    p = config.DATA_DIR / "cache" / LEVELS_FILE
    try:
        return p, json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return p, {}


def peak_db(path: str | None) -> float | None:
    """Pico real del archivo (dBFS). Se guarda en caché por ruta + tamaño."""
    if not path or not Path(path).exists():
        return None
    p, cache = _levels_cache()
    k = f"{path}|{Path(path).stat().st_size}"
    if k in cache:
        return cache[k]
    try:
        r = subprocess.run(["ffmpeg", "-v", "info", "-nostats", "-i", str(path), "-af", "volumedetect", "-f", "null", "-"],
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
        m = re.search(r"max_volume:\s*(-?[\d.]+) dB", r.stderr)
        val = float(m.group(1)) if m else None
    except (OSError, subprocess.TimeoutExpired):
        val = None
    cache[k] = val
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(cache), encoding="utf-8")
    except OSError:
        pass
    return val


def events(tl: dict) -> list[tuple[float, str]]:
    """Momentos que justifican un efecto, en tiempo del timeline."""
    ev = []
    for i, s in enumerate(tl["tracks"]["video"]):
        if i and s.get("transition", "cut") != "cut":
            ev.append((float(s["start"]), f"transición {s['transition']}"))
        for f in s.get("fx") or []:
            ev.append((float(s["start"]) + float(f.get("t", 0)), f"efecto {f.get('type')}"))
    for x in tl["tracks"].get("text") or []:
        ev.append((float(x["start"]), f"texto «{str(x.get('text', ''))[:30]}»"))
    for x in tl["tracks"].get("broll") or []:
        ev.append((float(x["start"]), "b-roll"))
    for x in tl["tracks"].get("memes") or []:
        ev.append((float(x.get("start", 0)), "meme"))
    return sorted(ev)


def reference_metrics(style_id: int | None) -> dict | None:
    """Métricas de tus videos de referencia (estilo aprendido)."""
    if not style_id:
        return None
    with db.get_conn() as conn:
        r = conn.execute("SELECT name, profile FROM learned_styles WHERE id = ?", (style_id,)).fetchone()
    if not r:
        return None
    try:
        prof = json.loads(r["profile"] or "{}")
    except ValueError:
        return None
    sp, e, snd = prof.get("sound_pro") or {}, prof.get("edit") or {}, prof.get("sound") or {}
    dur = float(prof.get("duration") or prof.get("window") or 60) or 60
    sfx_min = sp.get("sfx_per_min")
    if sfx_min is None and snd.get("sfx_estimate") is not None:
        sfx_min = float(snd["sfx_estimate"]) / max(min(dur, 90.0), 1) * 60
    return {"name": r["name"], "sfx_per_min": None if sfx_min is None else round(float(sfx_min), 2),
            "cuts_per_min": None if e.get("cuts_per_10s") is None else round(float(e["cuts_per_10s"]) * 6, 1),
            "music_bed": snd.get("music_bed") or sp.get("music"), "n_videos": prof.get("n_videos", 1)}


def edit_metrics(tl: dict) -> dict:
    mins = max(float(tl.get("duration") or 0) / 60, 1 / 60)
    segs = tl["tracks"]["video"]
    vis_cuts = sum(1 for s in segs[1:] if s.get("transition", "cut") != "cut") + len(tl["tracks"].get("broll") or [])
    fx = sum(len(s.get("fx") or []) for s in segs) + sum(1 for s in segs if s.get("zoom", 1) > 1)
    return {"sfx_per_min": round(len(tl["tracks"]["sfx"]) / mins, 2), "visual_changes_per_min": round(vis_cuts / mins, 1),
            "fx_per_min": round(fx / mins, 1), "texts_per_min": round(len(tl["tracks"].get("text") or []) / mins, 1),
            "music": bool(tl["tracks"].get("music"))}


def listen(render_path: str | None, sfx: list[dict], duration: float) -> dict[str, float]:
    """Escucha un render: cuánto (LU) sobresale cada efecto respecto a tu voz alrededor. {sfx_id: exceso}"""
    if not render_path or not Path(render_path).exists() or not sfx:
        return {}
    try:
        r = subprocess.run(["ffmpeg", "-v", "verbose", "-nostats", "-i", str(render_path), "-vn",
                            "-af", "ebur128=framelog=verbose", "-f", "null", "-"],
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=900)
    except (OSError, subprocess.TimeoutExpired):
        return {}
    pts = [(float(t), float(m)) for t, m in re.findall(r"t:\s*([\d.]+)\s+TARGET.*?M:\s*(-?[\d.]+)", r.stderr)]
    if len(pts) < 20:
        return {}
    out = {}
    for x in sfx:
        t = float(x["start"])
        near = [m for tt, m in pts if t - 4 <= tt <= t + 4 and not (t - 0.1 <= tt <= t + 0.9) and m > -50]
        hit = [m for tt, m in pts if t - 0.1 <= tt <= t + 0.9]
        if len(near) >= 8 and hit:
            near.sort()
            ref = near[len(near) // 2]
            out[x["id"]] = round(max(hit) - ref, 1)
    return out


JUDGE_SCHEMA = {
    "type": "object",
    "properties": {
        "verdicts": {"type": "array", "items": {"type": "object", "properties": {
            "id": {"type": "string"}, "action": {"type": "string", "enum": ["mantener", "quitar", "bajar"]},
            "reason": {"type": "string"}}, "required": ["id", "action"]}},
        "score": {"type": "number"}, "summary": {"type": "string"}},
    "required": ["verdicts"],
}


def _llm_review(tl: dict, rows: list[dict], kind: str, edit_style: str, model_role: str = "director") -> dict | None:
    from ..ai import llm
    url, model = db.get_setting("ollama_url"), db.role_model(model_role)
    try:
        st = llm.status(url)
        if not st["available"] or model not in st["models"]:
            return None
    except Exception:  # noqa: BLE001
        return None
    items = []
    for x in rows:
        t = x["start"]
        said = " ".join(w["word"] for w in tl.get("words") or [] if t - 3 <= w["start"] <= t + 2)[:220]
        items.append({"id": x["id"], "t": round(t, 1), "sonido": x["title"], "tipo": x["cat"], "motivo": x["why"],
                      "se_dice": said})
    system = (
        "Eres un juez de sonido profesional de YouTube. Revisas efectos de sonido de una edición. "
        f"Tipo de video: {kind}. Estilo de edición: {edit_style}. "
        "Un efecto solo se queda si refuerza algo que se ve o se dice en ese instante; si distrae, suena "
        "chistoso en un tema serio, interrumpe una frase importante o se repite, se quita. Si encaja pero "
        "suena encima de la voz, 'bajar'. Sé estricto: en videos serios o informativos, menos es más. "
        "Responde en español, razones de máximo 12 palabras.")
    try:
        out = llm.chat_json(url, model, system, json.dumps(items, ensure_ascii=False), JUDGE_SCHEMA, max_tokens=1800)
    except Exception:  # noqa: BLE001
        return None
    return out if isinstance(out, dict) else None


def judge(tl: dict, st: dict, *, video_type: str = "", edit_style: str = "", style_id: int | None = None,
          render_path: str | None = None, use_llm: bool = True) -> dict:
    """Revisa y corrige tl["tracks"]["sfx"] en su lugar. Devuelve el reporte."""
    sfx = tl["tracks"]["sfx"]
    before = len(sfx)
    long_video = video_type == "long" or (not video_type and ((tl.get("width", 1080) > tl.get("height", 1920))
                                                              or float(tl.get("duration") or 0) > 180))
    calm = st.get("memes") is False or st.get("transition_style") == "cortes" or edit_style in (
        "informativo", "tutorial", "podcast")
    funny = edit_style in ("entretenimiento", "viral", "gaming") and st.get("memes") is not False
    ref = reference_metrics(style_id)
    mins = max(float(tl.get("duration") or 0) / 60, 1 / 60)
    target = st.get("sfx_per_min") or None
    if ref and ref.get("sfx_per_min") is not None:
        target = ref["sfx_per_min"]  # lo que hacen tus referencias manda
    if target is None:
        target = (0.8 if calm else 2.0) if long_video else (2.5 if calm else 6.0)
    if calm:
        target = min(target, 1.5 if long_video else 3.0)
    budget = max(0, int(round(target * mins)))
    gap = (20.0 if calm else 8.0) if long_video else (5.0 if calm else 2.5)

    ev = events(tl)
    changes: list[dict] = []
    rows = []
    for x in sorted(sfx, key=lambda x: x["start"]):
        a = assets.get_asset(x["asset_id"]) if x.get("asset_id") else None
        cat = category(a)
        near = [d for t, d in ev if -0.35 <= t - x["start"] <= 0.45]
        title = (a or {}).get("title") or f"sonido {x.get('asset_id')}"
        why = near[0] if near else None
        if not why and not calm:  # en estilos dinámicos vale reforzar una palabra dicha justo ahí
            w = next((w for w in tl.get("words") or [] if -0.3 <= w["start"] - x["start"] <= 0.35), None)
            if w:
                why = f"énfasis en «{str(w.get('word', '')).strip()[:20]}»"
        reason = None
        if a is None or not a.get("available", True):
            reason = "el archivo ya no existe"
        elif (a.get("duration") or 0) > 4:
            reason = "es demasiado largo para un efecto (parece una canción o ambiente)"
        elif cat == "meme" and not funny:
            reason = "sonido de meme en un video que no es de humor"
        elif cat == "fuerte" and calm and long_video:
            reason = "golpe fuerte en un video serio"
        elif not why:
            reason = "no pasa nada en pantalla en ese momento (estaba puesto al azar)"
        if reason:
            changes.append({"t": round(x["start"], 1), "sonido": title, "accion": "quitado", "motivo": reason})
            continue
        prio = 3 if "texto" in why or "meme" in why else 2 if "efecto" in why else 0 if "énfasis" in why else 1
        rows.append({**x, "title": title, "cat": cat, "why": why, "prio": prio, "_a": a})

    # espacio mínimo y presupuesto: se quedan los mejor justificados
    rows.sort(key=lambda r: (-r["prio"], r["start"]))
    kept: list[dict] = []
    for r in rows:
        if len(kept) >= budget:
            changes.append({"t": round(r["start"], 1), "sonido": r["title"], "accion": "quitado",
                            "motivo": f"sobran efectos: tu estilo usa ~{target:g} por minuto"})
        elif any(abs(r["start"] - k["start"]) < gap for k in kept):
            changes.append({"t": round(r["start"], 1), "sonido": r["title"], "accion": "quitado",
                            "motivo": f"muy pegado a otro efecto (mínimo {gap:g} s entre efectos)"})
        else:
            kept.append(r)
    kept.sort(key=lambda r: r["start"])

    llm_info = None
    if use_llm and kept:
        llm_info = _llm_review(tl, kept, "video largo de YouTube" if long_video else "clip corto vertical", edit_style or "—")
        if llm_info:
            verdict = {str(v.get("id")): v for v in llm_info.get("verdicts") or []}
            nxt = []
            for r in kept:
                v = verdict.get(r["id"])
                if v and v.get("action") == "quitar":
                    changes.append({"t": round(r["start"], 1), "sonido": r["title"], "accion": "quitado",
                                    "motivo": "juez: " + str(v.get("reason") or "no aporta")[:90]})
                    continue
                if v and v.get("action") == "bajar":
                    r["_lower"] = str(v.get("reason") or "tapa la voz")[:90]
                nxt.append(r)
            kept = nxt

    # volumen por el pico real del archivo: ninguno debe tapar la voz
    peak_target = -14.0 if calm else -10.0
    heard = listen(render_path, kept, float(tl.get("duration") or 0)) if render_path else {}
    for r in kept:
        vol0 = float(r.get("volume", 0.5))
        pk = peak_db((r["_a"] or {}).get("path"))
        vol = vol0
        if pk is not None:
            vol = min(vol, 10 ** ((peak_target - pk) / 20))
        if r.get("_lower"):
            vol *= 0.6
        exc = heard.get(r["id"])
        if exc is not None and exc > 4:  # en el render sobresale de tu voz
            vol *= 10 ** (-(exc - 3) / 20)
        vol = round(max(0.05, min(0.8, vol)), 2)
        if abs(vol - vol0) >= 0.03:
            why = r.get("_lower") or (f"sobresalía {exc:g} LU sobre tu voz" if exc and exc > 4 else "volumen ajustado a su pico real")
            changes.append({"t": round(r["start"], 1), "sonido": r["title"], "accion": f"volumen {vol0:.2f}→{vol:.2f}",
                            "motivo": why})
        r["volume"] = vol

    tl["tracks"]["sfx"] = [{k: v for k, v in r.items() if k in ("id", "asset_id", "start", "volume")} for r in kept]
    after = edit_metrics(tl)
    cmp = []
    if ref:
        if ref.get("sfx_per_min") is not None:
            cmp.append({"que": "Efectos de sonido por minuto", "tuyo": after["sfx_per_min"], "referencia": ref["sfx_per_min"]})
        if ref.get("cuts_per_min") is not None:
            cmp.append({"que": "Cambios visuales por minuto", "tuyo": after["visual_changes_per_min"],
                        "referencia": ref["cuts_per_min"]})
        if ref.get("music_bed"):
            cmp.append({"que": "Música de fondo", "tuyo": "sí" if after["music"] else "no", "referencia": ref["music_bed"]})
    removed = sum(1 for c in changes if c["accion"] == "quitado")
    score = 10.0 - min(6.0, removed * 0.6) - min(2.0, sum(1 for c in changes if c["accion"] != "quitado") * 0.2)
    if llm_info and isinstance(llm_info.get("score"), (int, float)):
        score = (score + float(llm_info["score"])) / 2
    return {"before": before, "after": len(kept), "budget": budget, "target_per_min": round(float(target), 2),
            "min_gap_s": gap, "changes": sorted(changes, key=lambda c: c["t"]), "reference": ref, "compare": cmp,
            "metrics": after, "listened": bool(heard), "llm": bool(llm_info),
            "summary": (llm_info or {}).get("summary") or (
                f"Quedan {len(kept)} de {before} efectos" + (f"; {removed} quitados" if removed else "")),
            "score": round(max(0.0, min(10.0, score)), 1)}

