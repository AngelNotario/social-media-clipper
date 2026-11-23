"""Edición generativa: llena el video con recursos reales de internet según lo que dices, imitando el estilo de
tu referencia, y se autoevalúa hasta parecerse al menos un 80 % (adaptado a tu contenido, sin copiarlo).

Flujo (una sola búsqueda, varias rondas baratas de colocación):
1. Metas: del estilo aprendido (referencia) o de un preset por tipo de video; arranca con los ajustes que mejor
   funcionaron antes para ese estilo (aprendizaje progresivo).
2. Plan + búsqueda: el planificador decide qué mostrar en cada momento (logos, capturas, productos, clips,
   textos clave) y el buscador trae recursos verificados (y los deja indexados para otros videos).
3. Colocación: con pantalla verde y cámara protegida → fondo por tramos (como un video de comentario donde todo
   el fondo cambia cada pocos segundos); sin pantalla verde → tarjetas animadas a un lado de tu cara.
4. Juez de estilo: compara métricas con la referencia; si no llega a 80 %, ajusta densidad/textos/zooms y
   vuelve a colocar (sin volver a buscar). Guarda los mejores ajustes para la próxima vez.
"""
from __future__ import annotations

import math

from . import timeline as tlm

GOAL = 90.0
MAX_ROUNDS = 8
SPAN = (2.5, 7.5)  # duración de cada imagen de fondo


def _rate(targets: dict, *keys, default: float) -> float:
    for k in keys:
        v = targets.get(k)
        if isinstance(v, (int, float)) and v > 0 and math.isfinite(v):
            return float(v)
    return default


def _clear(tl: dict) -> None:
    """Quita lo que puso una ronda anterior (marcado gen=True) para recolocar."""
    tl["tracks"]["broll"] = [b for b in tl["tracks"]["broll"] if not b.get("gen")]
    tl["tracks"]["text"] = [t for t in tl["tracks"]["text"] if not t.get("gen")]
    tl["tracks"]["sfx"] = [s for s in tl["tracks"]["sfx"] if not s.get("gen")]
    ch = tl.get("chroma") or {}
    if ch.get("bg_schedule"):
        ch["bg_schedule"] = [e for e in ch["bg_schedule"] if not e.get("gen")]


def _entry_from(item: dict) -> dict | None:
    r = item.get("resource") or {}
    if r.get("kind") == "text":
        return {"kind": "text", "text": str(r.get("text") or "")[:40].upper(), "sub": r.get("sub") or "",
                "color": r.get("color") or "#FFFFFF", "bg_color": r.get("bg_color") or "#E8424F"}
    if not r.get("asset_id"):
        return None
    e = {"asset_id": r["asset_id"], "fit": r.get("fit") or ("contain" if item["need"].get("what") in (
        "logo", "captura_web", "producto", "noticia") else "cover")}
    if r.get("bg_color"):
        e["bg_color"] = r["bg_color"]
    if r.get("media_type") == "video":
        e["src_in"] = 0.0
    else:
        e["kenburns"] = True
    return e


def place_background(tl: dict, items: list[dict], per_min: float, text_share: float) -> int:
    """Fondo por tramos detrás de tu cámara: cambia cada pocos segundos según lo que dices."""
    dur = float(tl["duration"])
    want = max(1, int(round(per_min * dur / 60)))
    usable = [it for it in items if _entry_from(it)]
    texts = [it for it in usable if (it.get("resource") or {}).get("kind") == "text"]
    media = [it for it in usable if it not in texts]
    n_text = min(len(texts), int(round(want * text_share)))
    pick = sorted(media[: max(0, want - n_text)] + texts[:n_text], key=lambda it: float(it["need"]["t0"]))
    sched, last_end = [], 0.0
    for i, it in enumerate(pick):
        t0 = max(float(it["need"]["t0"]), last_end)
        nxt = float(pick[i + 1]["need"]["t0"]) if i + 1 < len(pick) else dur
        t1 = min(max(float(it["need"]["t1"]), t0 + SPAN[0]), t0 + SPAN[1], nxt if nxt > t0 + SPAN[0] else t0 + SPAN[0], dur)
        if t1 - t0 < 1.5:
            continue
        e = _entry_from(it)
        e.update(id=tlm.nid("g"), start=round(t0, 2), end=round(t1, 2), anim="cut" if i % 3 else "fade", gen=True)
        sched.append(e)
        last_end = t1
    ch = tl.setdefault("chroma", {})
    ch["bg_schedule"] = [e for e in (ch.get("bg_schedule") or []) if not e.get("gen")] + sched
    return len(sched)


def place_cards(tl: dict, items: list[dict], per_min: float, face_side: str = "center", zone: str = "sides",
                plan: dict | None = None) -> int:
    """Tarjetas animadas sin tapar tu cara.
    zone "sides": video horizontal, del lado contrario a tu cara.
    zone "top": clip vertical con tu cara al centro; las tarjetas van arriba (encima de la cabeza).
    zone "game": clip vertical estilo stream (cámara arriba, juego abajo); van sobre el juego."""
    dur = float(tl["duration"])
    want = max(1, int(round(per_min * dur / 60)))
    plan = plan or {}
    # lo que ya está en pantalla (b-roll de la IA editora) no se repite ni se encima
    busy = [(float(b["start"]), float(b["end"]), b.get("asset_id")) for b in tl["tracks"]["broll"] if not b.get("gen")]
    used = {a for _, _, a in busy if a}
    media = [it for it in items if (it.get("resource") or {}).get("asset_id") and it["resource"]["asset_id"] not in used]
    pick = sorted(media[:want], key=lambda it: float(it["need"]["t0"]))
    max_len = float(plan.get("max_len") or (6.0 if zone == "sides" else 3.5))  # en shorts las tarjetas duran menos
    gap = float(plan.get("gap") or (2.0 if zone == "sides" else 1.2))
    mode = plan.get("mode") or "card"
    n, last = 0, -99.0
    for i, it in enumerate(pick):
        t0, t1 = float(it["need"]["t0"]), float(it["need"]["t1"])
        if t0 - last < gap or any(a - 0.5 < t1 and t0 < e + 0.5 for a, e, _ in busy):
            continue
        r = it["resource"]
        lab = (it["need"].get("label") or "")[:32]
        if zone == "sides":
            side = "right" if face_side == "left" else "left" if face_side == "right" else ("right" if i % 2 else "left")
            pos, scale = {"x": 0.72 if side == "right" else 0.28, "y": 0.42}, 0.4
            rot = (-4 if side == "right" else 4) if i % 3 else 0
            anim = "pop" if i % 2 else ("slide_right" if side == "right" else "slide_left")
        elif zone == "game":
            pos, scale, rot = {"x": 0.5, "y": 0.72}, 0.62, (3 if i % 2 else -3) if i % 3 else 0
            anim = "pop" if i % 2 else "slide_up"
        else:  # top
            pos, scale, rot = {"x": 0.5, "y": 0.17}, 0.52, (3 if i % 2 else -3) if i % 3 else 0
            anim = "pop" if i % 2 else ("slide_left" if i % 4 == 0 else "slide_right")
        nxt = float(pick[i + 1]["need"]["t0"]) - gap if i + 1 < len(pick) else dur
        end = round(min(max(t1, t0 + min(max_len, 4.0)), t0 + max_len, max(nxt, t0 + 1.0), dur), 2)
        if end - t0 < 1.0:
            continue
        if mode == "pip" and zone == "sides":  # ventana pequeña en la esquina contraria a tu cara
            pos = {"x": 0.8 if pos["x"] > 0.5 else 0.2, "y": 0.28}
            scale, rot, anim = float(plan.get("scale") or 0.32), 0, "pop"
        tl["tracks"]["broll"].append({
            "id": tlm.nid("b"), "asset_id": r["asset_id"], "start": round(t0, 2), "end": end,
            "src_in": 0.0, "mode": mode, "pos": pos, "scale": scale, "rot": rot, "anim": anim,
            "frame": "white" if mode == "card" else "shadow", "label": lab, "volume": 0.0, "gen": True})
        last = end
        n += 1
    return n


def add_labels(tl: dict, items: list[dict], per_min: float) -> int:
    """Rótulos cortos con datos que dices (sin inventar): usan el "label" del plan."""
    dur = float(tl["duration"])
    want = int(round(per_min * dur / 60))
    have = [t for t in tl["tracks"]["text"] if t.get("kind") in ("lower", "callout") and not t.get("gen")]
    n = 0
    for it in sorted(items, key=lambda it: -int(it["need"].get("priority") or 1)):
        if len(have) + n >= want:
            break
        lab = (it["need"].get("label") or "").strip()
        t0 = float(it["need"]["t0"])
        if not lab or any(abs(t0 - x["start"]) < 6 for x in tl["tracks"]["text"]):
            continue
        tl["tracks"]["text"].append({"id": tlm.nid("t"), "start": round(t0 + 0.2, 2), "end": round(min(t0 + 3.2, dur), 2),
                                     "text": lab, "kind": "lower", "gen": True})
        n += 1
    return n


def add_change_sfx(tl: dict, per_min: float) -> int:
    """Un whoosh suave en algunos cambios de recurso (el juez de sonido decide después si se quedan)."""
    from . import assets
    if per_min <= 0:
        return 0
    wh = assets.find_by_tag("sfx", "whoosh")
    if not wh:
        return 0
    times = sorted([float(e["start"]) for e in (tl.get("chroma") or {}).get("bg_schedule") or [] if e.get("gen")] +
                   [float(b["start"]) for b in tl["tracks"]["broll"] if b.get("gen")])
    gap, last, n = max(8.0, 60.0 / per_min), -1e9, 0
    for t in times:
        if t - last >= gap and t > 1.0:
            tl["tracks"]["sfx"].append({"id": tlm.nid("s"), "asset_id": wh["id"], "start": round(max(0.0, t - 0.1), 2),
                                        "volume": 0.25, "gen": True})
            last, n = t, n + 1
    return n


def apply_zoom(tl: dict, zoom_per_min: float) -> None:
    """Zooms suaves en tomas alternas según la meta (golpes de zoom = fx punch)."""
    if zoom_per_min <= 0:
        return
    gap = max(6.0, 60.0 / zoom_per_min)
    last = -1e9
    for s in tl["tracks"]["video"]:
        fx = [f for f in s.get("fx") or [] if not f.get("gen")]
        if s["start"] - last >= gap and (s["src_out"] - s["src_in"]) >= 2.0:
            fx.append({"t": 0.1, "type": "punch", "amount": 0.08, "hold": min(2.2, s["src_out"] - s["src_in"] - 0.2), "gen": True})
            last = s["start"]
        s["fx"] = fx


def _brief_report(rep: dict | None) -> dict:
    """Lo esencial del reporte del juez estricto (sin métricas completas, para no inflar el resultado)."""
    rep = rep or {}
    return {"resumen": rep.get("resumen"), "problemas": [
        {k: p.get(k) for k in ("key", "why_es")} for p in (rep.get("problemas") or [])[:8]]}


def run(tl: dict, ctx: dict, progress=lambda p, s: None) -> dict:
    """ctx: {style_id, video_type, edit_style, understood, research, chroma_protected, face_side, use_web, music_pref}"""
    from .. import resources
    from . import style_judge as sj
    targets = sj.targets_for(ctx.get("style_id"), ctx.get("video_type") or "long", ctx.get("edit_style") or "informativo")
    prev = sj.load_tuning(ctx.get("style_id"), ctx.get("edit_style") or "")
    tuning = (prev or {}).get("params") or {}
    full, card = targets.get("fullscreen_ratio"), targets.get("card_ratio")
    # fondo por tramos si hay pantalla verde con tu cámara protegida y la referencia usa recursos a pantalla
    # completa; si la referencia usa sobre todo tarjetas, tarjetas (también sobre el fondo verde reemplazado)
    bg_mode = bool(ctx.get("chroma_protected")) and not (card is not None and full is not None and card > full)
    sfx_rate = targets.get("sfx_per_min") if isinstance(targets.get("sfx_per_min"), (int, float)) else 1.0
    zone = ctx.get("card_zone") or "sides"
    use_labels = ctx.get("labels", True)  # en clips el rótulo va en la propia tarjeta (no choca con subtítulos)
    use_zoom = ctx.get("zoom", True)      # en clips los zooms ya los pone el director
    vis = _rate(targets, "visual_changes_per_min", "overlays_per_min", "cuts_per_min", default=4.0)
    params = {
        "visual_per_min": float(tuning.get("visual_per_min") or min(vis, 16.0)),
        "text_share": float(tuning.get("text_share") or (0.15 if (targets.get("text_per_min") or 0) > 0.5 else 0.08)),
        "labels_per_min": float(tuning.get("labels_per_min") or _rate(targets, "text_per_min", default=1.0)),
        "zoom_per_min": float(tuning["zoom_per_min"]) if isinstance(tuning.get("zoom_per_min"), (int, float)) else (
            float(targets["zoom_per_min"]) if isinstance(targets.get("zoom_per_min"), (int, float)) else 1.5),
    }
    if isinstance(targets.get("zoom_per_min"), (int, float)) and targets["zoom_per_min"] < 0.3:
        params["zoom_per_min"] = 0.0  # tu referencia no usa zooms: aunque un ajuste viejo diga otra cosa
    dens = float(ctx.get("density") or 1.0)  # «más/menos recursos» de tus instrucciones
    if dens != 1.0:
        params["visual_per_min"] = max(0.5, min(18.0, params["visual_per_min"] * dens))
    from . import reference_rules
    plan = reference_rules.card_plan(targets if ctx.get("style_id") else None, zone)
    progress(0.0, "Planeando qué mostrar en cada momento")
    plan_rate = params["visual_per_min"] * 1.35  # se planea de más: las rondas eligen cuántos usar
    items = resources.gather(tl, ctx.get("understood"), ctx.get("research"), plan_rate,
                             mode="background" if bg_mode else "cards",
                             allow_web=ctx.get("use_web", True), time_cap=float(ctx.get("time_cap") or 900),
                             max_items=int(min(120, plan_rate * float(tl["duration"]) / 60 + 4)),
                             progress=lambda p, s: progress(0.05 + 0.75 * p, s))
    found = [it for it in items if it.get("resource")]
    removed_assets: set = set()  # recursos que el juez estricto quitó (no vuelven al recolocar)

    def place(prm: dict) -> int:
        _clear(tl)
        pool = [it for it in found if (it.get("resource") or {}).get("asset_id") not in removed_assets]
        if bg_mode:
            n = place_background(tl, pool, prm["visual_per_min"], prm["text_share"])
        else:
            n = place_cards(tl, pool, prm["visual_per_min"], ctx.get("face_side") or "center", zone, plan)
        if use_labels:
            add_labels(tl, pool, prm["labels_per_min"])
        if use_zoom and prm["zoom_per_min"] > 0:
            apply_zoom(tl, prm["zoom_per_min"])
        add_change_sfx(tl, sfx_rate)
        return n

    rounds, best = [], None
    for rnd in range(1, MAX_ROUNDS + 1):
        placed = place(params)
        sim = sj.similarity(targets, sj.edit_metrics(tl))
        rounds.append({"round": rnd, "score": sim["score"], "placed": placed, "params": dict(params)})
        progress(0.82 + 0.04 * rnd, f"Juez de estilo: {sim['score']:.0f} % (ronda {rnd})")
        if best is None or sim["score"] > best[0]["score"]:
            best = (sim, dict(params))
        if sim["score"] >= GOAL:
            break
        moved = False
        for a in (sim.get("advice") or [])[:3]:  # los 3 ajustes de más impacto según el juez
            k, prm, up = a.get("key"), a.get("param"), a.get("action") == "more"
            f = 1.3 if up else 0.75
            if prm in ("broll_per_min", "broll_duration") or k in ("visual_changes_per_min", "overlays_per_min", "overlay_ratio"):
                params["visual_per_min"] = max(1.0, min(18.0, params["visual_per_min"] * f)); moved = True
            elif prm == "callouts_per_min" or k == "text_per_min":
                params["labels_per_min"] = max(0.0, min(4.0, params["labels_per_min"] * f + (0.3 if up else 0)))
                params["text_share"] = max(0.0, min(0.35, params["text_share"] * f + (0.03 if up else 0))); moved = True
            elif prm == "zoom_every" or k == "zoom_per_min":
                params["zoom_per_min"] = max(0.0, min(6.0, params["zoom_per_min"] * f + (0.3 if up else 0))); moved = True
        if not moved or (len(rounds) >= 2 and rounds[-1]["score"] == rounds[-2]["score"]
                         and rounds[-1]["placed"] == rounds[-2]["placed"]):
            break  # lo que falta no depende de los recursos: otra ronda igual no cambia nada
    # deja aplicada la mejor ronda
    sim, params = best
    place(params)
    try:  # aprendizaje progresivo: la próxima edición de este estilo arranca con lo que mejor funcionó
        sj.save_tuning(ctx.get("style_id"), ctx.get("edit_style") or "", params, sim["score"])
    except Exception:  # noqa: BLE001
        pass

    # Juez estricto con retroalimentación iterativa contra la referencia aprendida: revisa que cada texto y recurso
    # tenga soporte en lo que dices y, si cambia un parámetro, se vuelve a colocar con los recursos ya encontrados.
    score = sim["score"]
    rounds_info = list(rounds)
    strict = None
    if ctx.get("style_id"):
        from . import strict_judge

        def replace(prm: dict, drop_assets: set) -> None:
            removed_assets.update(a for a in drop_assets if a)
            place(prm)

        try:
            strict = strict_judge.run_strict(tl, params, dict(ctx), source_path=ctx.get("source_path"),
                                             progress=progress, replace=replace)
            sim2 = sj.similarity(targets, sj.edit_metrics(tl))
            score = strict["score"] if strict["aprobado"] else sim2["score"]
            rounds_info = rounds + [{"round": strict["ronda"], "score": score, "strict": True,
                                     "aprobado": strict["aprobado"], "reporte": _brief_report(strict.get("reporte"))}]
            sim = sim2
        except Exception as exc:  # noqa: BLE001 — el juez estricto nunca tumba la edición
            rounds_info = rounds + [{"strict": True, "error": str(exc)[:200]}]

    goal = GOAL
    return {"score": score, "goal": goal, "reached": score >= goal, "rounds": rounds_info,
            "strict_approved": bool(strict and strict.get("aprobado")),
            "dims": sim.get("dims"), "advice": sim.get("advice"), "mode": "fondo" if bg_mode else "tarjetas",
            "planned": len(items), "found": len(found),
            "reused": sum(1 for it in found if (it.get("resource") or {}).get("reused")),
            "web": sum(1 for it in found if (it.get("resource") or {}).get("asset_id") and not it["resource"].get("reused")),
            "reference": targets.get("name"), "music_ref": targets.get("music")}
