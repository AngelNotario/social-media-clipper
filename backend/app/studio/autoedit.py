"""Auto-edición: de tu toma cruda a un primer corte con ritmo de video corto.

1. Transcribe (Whisper, tiempos por palabra).
2. Quita silencios largos y muletillas → segmentos ("jump cuts").
3. Zooms alternados ("punch-in") y énfasis.
4. Encuadre al rostro, whoosh en cambios de plano.
5. La IA local añade título, textos destacados, b-roll y música de tu biblioteca.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from .. import config, db
from ..ai import audio, transcribe
from ..editor import crop
from . import agent, assets, brief as brief_mod, chroma, learn, projects
from . import style as style_mod
from . import timeline as tlm

STRONG_FILLERS = {"eh", "ehh", "ehm", "em", "emm", "mm", "mmm", "mmh", "hmm", "ah", "ahh", "um", "uh", "eeh", "aah"}
WEAK_FILLERS = {"este", "pues", "bueno", "osea"}


def _norm(word: str) -> str:
    return re.sub(r"[^\wáéíóúñü]", "", word.lower())


def keep_words(words: list[dict], remove_fillers: bool = True) -> list[dict]:
    if not remove_fillers:
        return list(words)
    out = []
    for i, w in enumerate(words):
        n = _norm(w["word"])
        if n in STRONG_FILLERS:
            continue
        if n in WEAK_FILLERS:
            gap_before = w["start"] - words[i - 1]["end"] if i > 0 else 1
            gap_after = words[i + 1]["start"] - w["end"] if i + 1 < len(words) else 1
            if gap_before > 0.3 and gap_after > 0.3:
                continue
        out.append(w)
    return out


def measured_zoom(st: dict) -> bool:
    """El estilo trae zooms medidos de una referencia: van en los momentos de énfasis, no alternados por toma."""
    return st.get("zoom_kind") in ("punch", "ramp", "none")


def build_segments(words: list[dict], duration: float, silence: float,
                   pad_in: float = 0.06, pad_out: float = 0.14, max_len: float = 7.0) -> list[tuple[float, float]]:
    if not words:
        return [(0.0, duration)]
    groups, cur = [], [words[0]]
    for prev, w in zip(words, words[1:]):
        gap = w["start"] - prev["end"]
        long_seg = w["end"] - cur[0]["start"] > max_len and re.search(r"[.!?…,]$", prev["word"])
        if gap > silence or long_seg:
            groups.append(cur)
            cur = [w]
        else:
            cur.append(w)
    groups.append(cur)
    segs: list[tuple[float, float]] = []
    for g in groups:
        a = max(0.0, g[0]["start"] - pad_in)
        b = min(duration, g[-1]["end"] + pad_out)
        if segs and a < segs[-1][1]:
            a = segs[-1][1]
        if b - a < 0.25:
            if segs:  # unir fragmentos diminutos al anterior
                segs[-1] = (segs[-1][0], max(segs[-1][1], b))
            continue
        segs.append((round(a, 3), round(b, 3)))
    return segs or [(0.0, duration)]


def _det(seed: str) -> float:
    """Pseudo-aleatorio determinista (mismo resultado en cada auto-edición)."""
    return int(hashlib.md5(seed.encode()).hexdigest()[:8], 16) / 0xFFFFFFFF


def build_timeline(p: dict, words: list[dict], energy: list[float], face_x: float | None,
                   track: list | None = None, target_s: float | None = None, cfg: dict | None = None,
                   video_type: str = "long", progress=lambda x, s: None) -> dict:
    st = p["style"]
    dur = p["source_meta"]["duration"]
    tl = tlm.empty(p["format"])
    kept = keep_words(words, st["remove_fillers"])
    segs = build_segments(kept, dur, st["silence_threshold"])
    condensed = None
    if target_s:  # duración final del brief: se queda con las mejores partes
        from . import condense
        segs, condensed = condense.select(segs, kept, energy, float(target_s), cfg, video_type, progress)
    is_video = p["source_kind"] == "video"
    zoom_amt, every = st["zoom_amount"], max(1, int(st["zoom_every"]))
    for i, (a, b) in enumerate(segs):
        zoom = 1.0
        if is_video and zoom_amt > 1.0 and not measured_zoom(st):
            zoom = zoom_amt if (i // every) % 2 == 1 else 1.0
            text = " ".join(w["word"] for w in kept if a <= w["start"] < b)
            if "!" in text or "?" in text:
                zoom = round(zoom_amt + 0.06, 3)
        seg = {"id": tlm.nid("v"), "src_in": a, "src_out": b, "start": 0,
               "zoom": zoom, "crop_x": face_x if face_x is not None else 0.5}
        if track:
            pts = [[p_[0], p_[1]] for p_ in track if a - 0.5 <= p_[0] <= b + 0.5]
            if len(pts) >= 2:
                seg["track"] = pts
            fy = [p_[2] for p_ in track if len(p_) > 2 and a - 0.5 <= p_[0] <= b + 0.5]
            if fy:  # altura de la cara: los zooms se centran en ella y no le cortan la cabeza
                seg["face_y"] = round(sorted(fy)[len(fy) // 2], 3)
        tl["tracks"]["video"].append(seg)
    tlm.relayout(tl, words, dur)

    # Transiciones según el estilo (sin alterar la duración)
    pattern = {"dinamico": ["zoom", "flash", "whip", "glitch"], "suave": ["fade"], "cortes": []}.get(
        st.get("transition_style", "dinamico"), [])
    whoosh = assets.find_by_tag("sfx", "whoosh")
    k = 0
    for i, s in enumerate(tl["tracks"]["video"]):
        s["transition"] = "cut"
        if i == 0 or not pattern:
            continue
        if _det(s["id"] + "t") < max(0.35, st["sfx_density"]):
            s["transition"] = pattern[k % len(pattern)]
            k += 1
            if whoosh and is_video and s["transition"] in ("whip", "zoom", "flash"):
                tl["tracks"]["sfx"].append({"id": tlm.nid("s"), "asset_id": whoosh["id"],
                                            "start": max(0.0, s["start"] - 0.12), "volume": 0.45})
    if condensed:
        tl["_condense"] = condensed
    return tl


def plan_chroma(p: dict, b: dict, old_tl: dict, tl: dict) -> dict | None:
    """Pantalla verde según el brief: respeta lo que ajustaste a mano; si no, la activa sola cuando detecta
    fondo verde y hay un video adjunto para el fondo (p. ej. un gameplay)."""
    old = old_tl.get("chroma")
    if b["chroma"] == "off" or p["source_kind"] != "video":
        return {**chroma.normalize(old), "enabled": False} if old else None
    if old and old.get("enabled"):
        ch = chroma.normalize(old)
        if "protect" not in old:  # proyectos de antes: se busca el recuadro de la cámara una vez
            ch["protect"] = chroma.detect_protect(p["source_path"], ch, chroma.sample_times(tl))
        return ch
    bg_att = next((a for a in b["attachments"] if a["role"] == "chroma_bg"), None)
    if not bg_att and b["chroma"] == "on":
        bg_att = next((a for a in b["attachments"] if a["role"] == "auto"), None)
    seg = (tl["tracks"]["video"] or [{}])[0]
    det = chroma.detect_color(p["source_path"], float(seg.get("src_in", 0)) + 1.0)
    if b["chroma"] == "auto" and not (det["detected"] and (bg_att or det.get("coverage", 0) >= 0.5)):
        return chroma.normalize(old) if old else None
    if b["chroma"] == "auto" and not bg_att:
        bg_att = next((a for a in b["attachments"] if a["role"] == "auto"), None)
    ch = chroma.normalize(old)
    ch["enabled"] = True
    if det["detected"]:
        ch["color"], ch["similarity"] = det["color"], det["similarity"]
        ch["protect"] = chroma.detect_protect(p["source_path"], chroma.normalize(ch), chroma.sample_times(tl))
    if bg_att:
        with db.get_conn() as conn:
            v = conn.execute("SELECT duration FROM videos WHERE id = ?", (bg_att["video_id"],)).fetchone()
        bdur = float(v["duration"] or 0) if v else 0
        ch["bg"] = {**ch["bg"], "kind": "video", "video_id": bg_att["video_id"], "asset_id": None,
                    "start": round(min(120.0, bdur * 0.08), 1), "volume": 0.0}
    return chroma.normalize(ch)


def run_autoedit(job: dict, progress) -> dict:
    params = job["params"] or {}
    pid = params["project_id"]
    p = projects.get(pid)
    if not p:
        raise RuntimeError("El proyecto ya no existe.")
    src = Path(p["source_path"])
    if not src.exists():
        raise RuntimeError("El archivo de tu toma ya no está disponible.")
    meta = p["source_meta"]
    assets.ensure_starter_pack()

    wav = config.WORK_DIR / f"studio_{pid}.wav"
    try:
        energy: list[float] = []
        if meta.get("has_audio"):
            progress(0.02, "Extrayendo audio")
            audio.extract_wav(src, wav)
            if not p.get("transcript") or params.get("retranscribe"):
                progress(0.05, "Transcribiendo tu voz con Whisper")
                tr = transcribe.transcribe(
                    wav, db.get_setting("whisper_model"), db.get_setting("whisper_language"), meta["duration"],
                    lambda x: progress(0.05 + 0.5 * x, f"Transcribiendo tu voz ({int(x * 100)}%)"))
                with db.get_conn() as conn:
                    conn.execute("UPDATE studio_projects SET transcript = ? WHERE id = ?",
                                 (json.dumps(tr, ensure_ascii=False), pid))
                p = projects.get(pid)
            energy = audio.energy_curve(wav)

        face_x, track = None, None
        if p["source_kind"] == "video":
            progress(0.58, "Siguiendo tu rostro para encuadrar")
            track = crop.face_track(src, 0, meta["duration"], step=0.25, with_y=True)
            face_x = sorted(p_[1] for p_ in track)[len(track) // 2] if track else None

        # El brief decide el ritmo: un video largo informativo no se edita como un TikTok
        b = brief_mod.infer(p)
        st, pace = brief_mod.style_for(p, b)
        # 1) Tu referencia manda desde el inicio: zooms, transiciones, subtítulos, memes y música salen de ella
        from . import instructions_plan, reference_rules
        from . import style_judge as sj
        targets = sj.targets_for(b["learned_style_id"], b["video_type"] or "long",
                                 b["edit_style"] or "informativo") if b["learned_style_id"] else None
        ref_edl = (learn.style_profile(b["learned_style_id"]) or {}).get("edl") or [] if b.get("learned_style_id") else []
        rules_notes = reference_rules.before(st, pace, b, targets)
        # 2) Tus instrucciones también, antes de editar (no al final, donde la IA las rechazaba en videos largos)
        iplan, inst_notes, flags = None, [], {"no_zoom": False, "resources": 1.0}
        if b["instructions"].strip() and params.get("use_ai", True) and agent.llm_ready():
            progress(0.6, "Entendiendo tus instrucciones")
            cfg = agent._llm_cfg()
            iplan = instructions_plan.plan(b["instructions"], cfg["url"], cfg["model"])
            inst_notes, flags = instructions_plan.apply(iplan, b, st, pace)
        st = style_mod.clean_style(st)
        with db.get_conn() as conn:
            conn.execute("UPDATE studio_projects SET style = ? WHERE id = ?", (json.dumps(st), pid))
        old_tl = p["timeline"]
        p["style"] = st

        progress(0.62, "Cortando silencios y muletillas")
        words = projects.source_words(p)
        cfg_c = agent._llm_cfg() if (b.get("target_duration") and params.get("use_ai", True) and agent.llm_ready()) else None
        tl = build_timeline(p, words, energy, face_x, track, b.get("target_duration"), cfg_c, b["video_type"],
                            lambda x, s: progress(0.62 + 0.05 * x, s))
        condensed = tl.pop("_condense", None)
        tl["captions"]["enabled"] = bool(pace["captions_on"])
        if old_tl.get("layout"):
            tl["layout"] = old_tl["layout"]
        tl["chroma"] = plan_chroma(p, b, old_tl, tl)
        removed = round(meta["duration"] - tl["duration"], 1)

        ai_info = {"used": False, "reference_rules": rules_notes, "condense": condensed,
                   "instructions_plan": {"resumen": (iplan or {}).get("resumen"), "aplicado": inst_notes,
                                         "puntuales": (iplan or {}).get("puntuales")} if iplan else None}
        if params.get("use_ai", True):
            try:
                warn = agent.llm_warning()
            except Exception:  # noqa: BLE001
                warn = None
            if warn:
                ai_info["warning"] = warn
                progress(0.68, warn[:120])
            progress(0.68, "La IA está añadiendo títulos, textos y recursos")
            bgvid = ((tl.get("chroma") or {}).get("bg") or {}).get("video_id") if (tl.get("chroma") or {}).get("enabled") else None
            b_for_ai = {**b, "attachments": [a for a in b["attachments"] if a["video_id"] != bgvid]}
            att_text, att_rows = brief_mod.attachments_for_llm(b_for_ai)
            extra = {"project_name": p.get("name"), "video_type": b["video_type"], "edit_style": b["edit_style"], "instructions": b["instructions"],
                     "attachments_text": att_text, "attachments": att_rows, "broll_every_s": pace["broll_every_s"],
                     "music": b["music"]}
            ai_info = {**ai_info, **agent.enrich(tl, p["style"], lambda x, s: progress(0.68 + 0.2 * x, s), extra=extra)}
            from .. import knowledge
            from . import director
            try:  # el director nunca debe tumbar la auto-edición
                ai_info["director"] = director.direct(
                    tl, p["style"], director.timeline_curve(tl, energy), None,
                    knowledge.prompt_context("studio", b["learned_style_id"] or params.get("style_id")), None,
                    lambda x, s: progress(0.88 + 0.08 * x, s), window=90.0)
            except Exception as exc:  # noqa: BLE001
                ai_info["director"] = {"error": str(exc)[:200]}
            if (b["music"] == "off" or (b["music"] == "auto" and (targets or {}).get("music") == "no")):
                tl["tracks"]["music"] = []  # sin música si tu referencia no tiene (antes dependía del generativo)
            if tl["tracks"]["music"] and p["style"].get("beat_sync", True):
                try:  # efectos al ritmo de la música elegida
                    ai_info["beats"] = director.apply_beats(tl)
                except Exception as exc:  # noqa: BLE001
                    ai_info["beats"] = {"error": str(exc)[:160]}
            if not flags["no_zoom"]:  # zooms medidos en tu referencia, en tus momentos de énfasis
                try:
                    zst = {**p["style"], **({"zoom_per_min": targets["zoom_per_min"]} if isinstance((targets or {}).get("zoom_per_min"), (int, float)) else {})}
                    ai_info["reference_zooms"] = reference_rules.place_zooms(tl, zst, director.timeline_curve(tl, energy))
                except Exception as exc:  # noqa: BLE001
                    ai_info["reference_zooms"] = {"error": str(exc)[:160]}
            caps_t = {**(targets or {}), **({"zoom_per_min": 0.0} if flags["no_zoom"] else {})}
            if caps_t:  # topes de la referencia sobre el director: se queda con los más fuertes, repartidos
                ai_info["reference_caps"] = reference_rules.after_director(tl, caps_t)
            gen_on = b.get("generative", "auto") == "on" or (b.get("generative", "auto") == "auto" and
                                                              (b["video_type"] == "long" or b["learned_style_id"]))
            if gen_on and flags["resources"] > 0 and agent.llm_ready():  # recursos reales + juez de estilo hasta 80 %
                from . import generative
                ck = tl.get("chroma") or {}
                fx_ = face_x if isinstance(face_x, (int, float)) else 0.5
                try:
                    ai_info["generative"] = generative.run(tl, {
                        "style_id": b["learned_style_id"], "video_type": b["video_type"], "edit_style": b["edit_style"],
                        "understood": ai_info.get("understood"), "research": ai_info.get("research"),
                        "chroma_protected": bool(ck.get("enabled") and ck.get("protect")),
                        "face_side": "left" if fx_ < 0.4 else "right" if fx_ > 0.6 else "center",
                        "use_web": True, "source_path": p["source_path"], "density": flags["resources"]},
                        lambda x, s_: progress(0.9 + 0.06 * x, s_))
                except Exception as exc:  # noqa: BLE001 — nunca tumba la auto-edición
                    ai_info["generative"] = {"error": str(exc)[:300]}
                music_ref = ((ai_info.get("generative") or {}).get("music_ref"))
                if b["music"] == "auto" and music_ref == "no":
                    tl["tracks"]["music"] = []
            if b["music"] == "off":
                tl["tracks"]["music"] = []
            if p["style"].get("fx_gap_s"):
                ai_info["fx_thinned"] = director.thin_fx(tl, float(p["style"]["fx_gap_s"]))
                if p["style"]["fx_gap_s"] >= 12:  # zoom alterno por toma también más espaciado
                    last = -1e9
                    for s in tl["tracks"]["video"]:
                        if s.get("zoom", 1.0) > 1.0:
                            if s["start"] - last < p["style"]["fx_gap_s"]:
                                s["zoom"] = 1.0
                            else:
                                last = s["start"]
            # Solo los cambios puntuales («pon un texto cuando digo X») van al final; lo general ya se aplicó arriba
            todo = "\n".join(iplan["puntuales"]) if iplan is not None else b["instructions"].strip()
            if todo and agent.llm_ready():
                progress(0.97, "Aplicando tus cambios puntuales")
                try:
                    tl, st2, msg, notes = agent.instruct(tl, p["style"], todo, source_path=p["source_path"])
                    ai_info["instructions"] = {"message": msg, "skipped": notes}
                    if st2 != p["style"]:
                        with db.get_conn() as conn:
                            conn.execute("UPDATE studio_projects SET style = ? WHERE id = ?", (json.dumps(st2), pid))
                except Exception as exc:  # noqa: BLE001
                    ai_info["instructions"] = {"error": str(exc)[:200]}
            tlm.relayout(tl, words, meta["duration"])
        if tl["tracks"]["sfx"]:  # juez de sonido: quita efectos al azar o fuera de estilo y ajusta volúmenes
            from . import sound_judge
            try:
                ai_info["sound"] = sound_judge.judge(
                    tl, p["style"], video_type=b["video_type"] or "", edit_style=b["edit_style"] or "",
                    style_id=b["learned_style_id"], use_llm=bool(params.get("use_ai", True)) and agent.llm_ready())
            except Exception as exc:  # noqa: BLE001
                ai_info["sound"] = {"error": str(exc)[:200]}
        if ai_info.get("research") or ai_info.get("understood"):  # se reutiliza al publicar y en las miniaturas
            with db.get_conn() as conn:
                r = conn.execute("SELECT source_meta FROM studio_projects WHERE id = ?", (pid,)).fetchone()
                sm = json.loads(r["source_meta"] or "{}")
                sm["understood"] = ai_info.get("understood")
                sm["research"] = ai_info.get("research")
                conn.execute("UPDATE studio_projects SET source_meta = ? WHERE id = ?", (json.dumps(sm, ensure_ascii=False), pid))
        ai_info["brief"] = {"video_type": b["video_type"], "edit_style": b["edit_style"],
                            "learned_style_id": b["learned_style_id"], "captions": tl["captions"]["enabled"],
                            "chroma": bool((tl.get("chroma") or {}).get("enabled"))}
        if b.get("learned_style_id") and ref_edl and agent.llm_ready():
            progress(0.985, "Ajustando la edición al estilo de referencia (bucle de agentes)")
            try:
                tl, edl_info = edit_to_style_loop(tl, p, st, ref_edl, targets or {},
                                                  words, float(meta["duration"]),
                                                  lambda x, m: progress(0.985 + 0.015 * x, m))
                ai_info["edl_loop"] = edl_info
            except Exception as exc:  # noqa: BLE001
                ai_info["edl_loop"] = {"error": str(exc)[:200]}
        tlm.relayout(tl, words, meta["duration"])
        p = projects.save(pid, tl, note="Auto-edición")
        return {"segments": len(tl["tracks"]["video"]), "duration": tl["duration"],
                "removed_seconds": removed, "ai": ai_info}
    finally:
        wav.unlink(missing_ok=True)


def _segment_at(tl: dict, t: float) -> dict | None:
    for s in tl["tracks"]["video"]:
        a = float(s.get("start", 0))
        ln = max(0.0, float(s.get("src_out", a)) - float(s.get("src_in", a)))
        if a - 0.001 <= t <= a + ln + 0.001:
            return s
    return None


def _segment_after(tl: dict, t: float) -> dict | None:
    cand = None
    for s in tl["tracks"]["video"]:
        a = float(s.get("start", 0))
        if a >= t - 0.001 and (cand is None or a < float(cand.get("start", 0))):
            cand = s
    return cand


def _first_asset(assets_mod, want: str) -> int | None:
    try:
        cat = assets_mod.catalog_for_llm() if hasattr(assets_mod, "catalog_for_llm") else []
    except Exception:
        cat = []
    for it in cat:
        tags = it.get("tags") or []
        kinds = str(it.get("kinds") or it.get("type") or "")
        if want in tags or want in kinds:
            try:
                return int(it["id"])
            except (TypeError, ValueError):
                pass
    return None


def _apply_corrections(tl: dict, corrections: list[dict], p: dict, st: dict, assets_mod) -> int:
    applied = 0
    dur = float(tl.get("duration") or 0)
    for c in corrections:
        op = c.get("op", "")
        t = max(0.0, min(float(c.get("t", 0)), max(dur, 0.001)))
        try:
            if op == "add_cut":
                if tlm.split_at(tl, t):
                    applied += 1
            elif op == "add_zoom":
                seg = _segment_at(tl, t)
                if seg:
                    amt = float((c.get("detail") or {}).get("amount") or st.get("zoom_amount") or 1.15)
                    seg["zoom"] = round(max(float(seg.get("zoom", 1.0)), amt), 3)
                    applied += 1
            elif op == "add_transition":
                seg = _segment_after(tl, t)
                if seg:
                    seg["transition"] = (c.get("detail") or {}).get("kind") or "zoom"
                    applied += 1
            elif op == "add_caption":
                end = min(float(c.get("end", t + 2.0)), dur)
                if end - t >= 0.25:
                    text = str((c.get("detail") or {}).get("text") or "...")
                    tl["tracks"]["text"].append({"id": tlm.nid("t"), "start": t, "end": round(end, 3),
                                                 "text": text[:120], "kind": "callout"})
                    applied += 1
            elif op.startswith("add_overlay_"):
                parts = op.split("_")
                mode = parts[2] if len(parts) >= 3 else "card"
                end = min(float(c.get("end", t + 2.5)), dur)
                if end - t >= 0.5:
                    aid = _first_asset(assets_mod, "broll")
                    tl["tracks"]["broll"].append({"id": tlm.nid("b"), "asset_id": aid,
                                                  "start": t, "end": round(end, 3), "mode": mode,
                                                  "label": (c.get("detail") or {}).get("label") or "",
                                                  "gen": aid is None})
                    applied += 1
            elif op == "add_sfx":
                aid = _first_asset(assets_mod, "sfx")
                if aid is not None:
                    tl["tracks"]["sfx"].append({"id": tlm.nid("s"), "asset_id": aid, "start": t, "volume": 0.5})
                    applied += 1
            elif op == "add_music":
                if not tl["tracks"]["music"]:
                    aid = _first_asset(assets_mod, "music")
                    if aid is not None:
                        tl["tracks"]["music"].append({"id": tlm.nid("m"), "asset_id": aid,
                                                      "start": 0.0, "end": round(dur, 3), "volume": 0.1})
                        applied += 1
        except Exception:  # noqa: BLE001
            continue
    return applied


def edit_to_style_loop(tl: dict, p: dict, st: dict, ref_edl: list[dict], targets: dict,
                       words: list[dict], dur: float, progress=lambda x, m: None,
                       max_rounds: int = 8, threshold: float = 90.0) -> tuple[dict, dict]:
    """Bucle de agentes: reconstruye el timeline, lo compara con el desarme frame-accurate del
    estilo de referencia, aplica correcciones y repite hasta que la edición coincida con el estilo
    (o se agoten las rondas). No improvisa: copia cortes, sonidos, textos y recursos en el segundo
    exacto donde están en la referencia."""
    from . import style_judge as sj
    best_tl, best_score, info = None, -1.0, {"rounds": 0, "score": 0.0, "by_type": {}}
    for rnd in range(max_rounds):
        tlm.relayout(tl, words, dur)
        mine = sj.edl_metrics(tl)
        metrics = sj.edit_metrics(tl)
        res = sj.edl_similarity(targets, metrics, ref_edl, mine, dur)
        score = float(res.get("score", 0.0))
        info = {"rounds": rnd + 1, "score": score, "by_type": res.get("edl_by_type", {}),
                "corrections": res.get("corrections", []), "advice": res.get("advice", [])}
        if best_tl is None or score > best_score:
            best_tl, best_score = tlm.deepcopy(tl), score
        if score >= threshold:
            break
        corrections = res.get("corrections", [])
        if not corrections:
            break
        applied = _apply_corrections(tl, corrections, p, st, assets)
        progress((rnd + 0.5) / max_rounds,
                 f"Ajustando estilo: ronda {rnd + 1}/{max_rounds} (score {score:.0f}, {applied} cambios)")
        if applied == 0:
            break
    tlm.relayout(tl, words, dur)
    info["best_score"] = best_score
    return (best_tl if best_tl is not None else tl), info
