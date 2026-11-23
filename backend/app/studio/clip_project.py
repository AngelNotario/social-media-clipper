"""Un clip como timeline del Studio: la misma edición sirve para verla y editarla en vivo o renderizarla.

Antes los clips tenían su propio motor de render y el Studio otro; ahora todo pasa por aquí:
- build(): arma el timeline (encuadre, subtítulos, título, edición del director, música y ritmo).
- create_project(): crea un proyecto del Studio con esa edición para revisarla y cambiarla antes de exportar.
"""
from __future__ import annotations

import json
from pathlib import Path

from .. import db
from ..ai.pipeline import load_transcript
from ..editor import crop
from . import agent, director, learn, projects
from . import style as style_mod
from . import timeline as tlm
from .autoedit import edit_to_style_loop

CROP_TO_LAYOUT = {"face": "crop", "center": "crop", "blur": "blur", "fit": "fit", "stream": "split"}


def _rows(clip_id: int) -> tuple[dict, dict]:
    with db.get_conn() as conn:
        c = conn.execute("SELECT * FROM clips WHERE id = ?", (clip_id,)).fetchone()
        if not c:
            raise ValueError("El clip ya no existe.")
        v = conn.execute("SELECT * FROM videos WHERE id = ?", (c["video_id"],)).fetchone()
    return dict(c), dict(v)


def style_for(opts: dict) -> dict:
    st = learn.style_for(opts["style_id"]) if opts.get("style_id") else \
        style_mod.clean_style(db.get_setting("studio_default_style") or None)
    st = dict(st)
    if opts.get("subtitles", True):
        if st["caption_style"] == "none" or opts.get("sub_style"):  # la casilla manda sobre el estilo aprendido
            st["caption_style"] = opts.get("sub_style") or "karaoke"
        if opts.get("sub_position") and opts["sub_position"] != "auto":
            st["caption_position"] = opts["sub_position"]
    if st.get("caption_position") == "bottom" and opts.get("sub_position", "auto") == "auto":
        # "abajo" heredado de un estilo = automático: en stream va bajo la cámara y no tapa el juego
        st["caption_position"] = "auto"
    for k in ("voice_mode", "pro_audio", "clean_voice", "beat_sync"):
        if k in opts:
            st[k] = opts[k]
    if opts.get("music"):
        st["music_volume"] = st.get("music_volume") or 0.12
        st["duck_music"] = True
    return style_mod.clean_style(st)


def build(clip_id: int, opts: dict, progress=lambda p, s: None) -> tuple[dict, dict, dict, dict]:
    """Devuelve (timeline, estilo, video, info) con la edición completa del clip."""
    c, v = _rows(clip_id)
    src = Path(v["stored_path"])
    if not src.exists():
        raise ValueError("El video original no se encuentra.")
    start, end = float(c["start_s"]), float(c["end_s"])
    mode = opts.get("crop_mode", "face")
    st = style_for(opts)
    ref_edl = (learn.style_profile(opts["style_id"]) or {}).get("edl") or [] if opts.get("style_id") else []
    targets = None
    if opts.get("style_id"):
        from . import style_judge as sj
        targets = sj.targets_for(opts["style_id"], "short", opts.get("edit_style") or "viral") or {}
    tl = tlm.empty("vertical")
    seg = {"id": tlm.nid("v"), "src_in": start, "src_out": end, "zoom": 1.0, "crop_x": 0.5}
    lay = {"mode": CROP_TO_LAYOUT.get(mode, "crop"), "game_x": 0.5,
           "src_ar": round((v["width"] or 16) / (v["height"] or 9), 4)}
    used = mode
    progress(0.02, "Siguiendo el rostro" if mode == "face" else "Preparando encuadre")
    if mode == "face":
        track = crop.face_track(src, start, end)
        if track:
            seg["crop_x"] = float(sorted(x for _, x in track)[len(track) // 2])  # fracción del ancho
            seg["track"] = [[float(t), float(x)] for t, x in track]
            used = "face (dinámico)"
        else:
            used = "center"
    elif mode == "stream":
        from .. import stream
        ss = stream.settings(v["id"])
        rect = ss["facecam"]
        use_cam = bool(rect) and ss["cam_mode"] != "never" and (
            ss["cam_mode"] == "always" or stream.camera_active(src, rect, start, end))
        if use_cam:
            lay["cam"] = {k: float(rect[k]) for k in ("x", "y", "w", "h")}
            used = "stream (cámara + juego)"
        else:
            lay["mode"] = "blur"
            used = "stream sin cámara (juego completo)"
    tl["layout"] = lay
    tl["tracks"]["video"] = [seg]
    tr = load_transcript(v["id"])
    src_words = [w for s in (tr["segments"] if tr else []) for w in (s.get("words") or [])]
    tlm.relayout(tl, src_words, v["duration"] or end)
    tl["captions"] = {"enabled": bool(opts.get("subtitles", True)) and st["caption_style"] != "none"}
    title = (opts.get("title") or c["hook"] or c["title"]) if opts.get("show_title") else None
    if title:
        tl["tracks"]["text"].append({"id": tlm.nid("t"), "start": 0.0, "end": round(min(3.2, tl["duration"]), 3),
                                     "text": title[:120], "kind": "title"})
    stats = {"punch": 0, "shake": 0, "meme": 0, "callout": 0, "sfx": 0, "cam": 0, "ai": False}
    if opts.get("viral", True):
        progress(0.03, "El director está editando el clip")
        from .. import knowledge
        with db.get_conn() as conn:
            a = conn.execute("SELECT energy FROM analyses WHERE video_id = ?", (v["id"],)).fetchone()
        energy = json.loads(a["energy"] or "[]") if a else []
        try:
            stats = director.direct(tl, st, director.timeline_curve(tl, energy), None,
                                    knowledge.prompt_context("clip", opts.get("style_id"), max_chars=3000), None,
                                    window=90.0)
        except Exception as exc:  # noqa: BLE001 — sin director el clip sale con encuadre y subtítulos
            stats["error"] = str(exc)[:200]
    if opts.get("music") and v["has_audio"] and director.music_bed(tl, st):
        m = tl["tracks"]["music"][0]
        from . import assets
        stats["music"] = (assets.get_asset(m["asset_id"]) or {}).get("title")
        if opts.get("beat_sync", True) and opts.get("viral", True):
            try:
                stats["beats"] = director.apply_beats(tl)
            except Exception as exc:  # noqa: BLE001
                stats["beats"] = {"error": str(exc)[:160]}
    tlm.relayout(tl, src_words, v["duration"] or end)
    gen = None
    if opts.get("generative", True):  # recursos reales de internet de lo que se dice en el clip
        gen = _generative(tl, st, c, opts, lay, progress)
        tlm.relayout(tl, src_words, v["duration"] or end)
    if opts.get("style_id") and ref_edl and agent.llm_ready():
        try:
            tl, edl_info = edit_to_style_loop(tl, {}, st, ref_edl, targets or {},
                                              src_words, float(v["duration"] or end),
                                              lambda x, m: progress(0.95 + 0.05 * x, m))
            gen = dict(gen or {})
            gen["edl_loop"] = edl_info
        except Exception as exc:  # noqa: BLE001 — sin ajuste exacto el clip sale con el estilo aplicado igual
            gen = dict(gen or {})
            gen["edl_loop"] = {"error": str(exc)[:200]}
    return tl, st, v, {"director": stats, "crop_used": used, "words": len(tl["words"]), "generative": gen}


def _generative(tl: dict, st: dict, c: dict, opts: dict, lay: dict, progress) -> dict | None:
    """Edición generativa en clips verticales: tarjetas con logos, capturas, productos o clips de lo que dices,
    arriba de tu cara (o sobre el juego en clips de stream), sin tapar los subtítulos. Lo encontrado queda en el
    índice de Recursos, así que los demás clips del mismo video casi no tienen que buscar."""
    from . import agent, generative, sound_judge
    if not tl.get("words") or not agent.llm_ready():
        return {"skipped": "sin transcripción o sin IA disponible"}
    zone = "game" if lay.get("mode") == "split" and lay.get("cam") else "top"
    ctx = {"style_id": opts.get("style_id"), "video_type": "short", "edit_style": opts.get("edit_style") or "viral",
           "understood": {"tema": c.get("title") or "", "postura": c.get("reason") or "", "gancho": c.get("hook") or "",
                          "hechos": []},
           "research": None, "chroma_protected": False, "card_zone": zone, "labels": False, "zoom": False,
           "use_web": True, "time_cap": 900}
    try:
        info = generative.run(tl, ctx, lambda x, s_: progress(0.05 + 0.25 * x, s_))
    except Exception as exc:  # noqa: BLE001 — sin recursos el clip sale igual
        return {"error": str(exc)[:300]}
    if tl["tracks"]["sfx"]:  # los whoosh nuevos + los del director pasan por el juez de sonido
        try:
            info["sound"] = sound_judge.judge(tl, st, video_type="short", edit_style=ctx["edit_style"],
                                              style_id=opts.get("style_id"), use_llm=False)
        except Exception as exc:  # noqa: BLE001
            info["sound"] = {"error": str(exc)[:160]}
    return info


def create_project(clip_id: int, opts: dict, progress=lambda p, s: None) -> dict:
    """Proyecto del Studio con la edición de la IA, listo para verlo y editarlo en vivo."""
    c, _ = _rows(clip_id)
    tl, st, v, info = build(clip_id, opts, progress)
    from .probe import probe_any
    meta = {**probe_any(Path(v["stored_path"])), "clip_id": clip_id}  # para publicar: gancho y motivo del clip
    tr = load_transcript(v["id"])  # el proyecto guarda la transcripción: al editar, los subtítulos se recalculan
    with db.get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO studio_projects (name, format, source_path, source_kind, source_meta, timeline, style, transcript) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (f"Clip: {c['title']}"[:120], "vertical", v["stored_path"], "video", json.dumps(meta), json.dumps(tl),
             json.dumps(st), json.dumps(tr) if tr else None))
        pid = cur.lastrowid
    projects.save(pid, note="Edición de la IA")
    return {"project_id": pid, **info}
