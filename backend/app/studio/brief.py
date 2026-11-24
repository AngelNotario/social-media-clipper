"""Brief del proyecto: qué tipo de video es y cómo se quiere editar, antes de auto-editar.

Se guarda en source_meta["brief"] (sobrevive a cada auto-edición). Decide:
- tipo: "short" (TikTok/Reels/Shorts) o "long" (video de YouTube); define el ritmo de toda la edición.
- estilo de edición: informativo, entretenimiento, gaming, tutorial, podcast, vlog o viral.
- estilo aprendido (de tus referencias) para el look y el sonido; el tipo de video pone los límites sensatos.
- subtítulos, música y pantalla verde (auto / sí / no), instrucciones libres y videos de la biblioteca adjuntos.

Reglas de ritmo (guías de edición para retención): en videos largos algo cambia en pantalla cada 15-30 s y hay
b-roll cada 30-60 s, con transiciones simples; en shorts, cambios cada pocos segundos.
"""
from __future__ import annotations

from .. import db
from . import style as style_mod

VIDEO_TYPES = ("short", "long")
EDIT_STYLES = ("informativo", "entretenimiento", "gaming", "tutorial", "podcast", "vlog", "viral")
ROLES = ("chroma_bg", "broll", "auto")
TRI = ("auto", "on", "off")
CAPTION_LOOKS = ("auto", "clean", "karaoke", "simple")

DEFAULT = {"video_type": None, "edit_style": None, "learned_style_id": None, "captions": "auto",
           "caption_look": "auto", "music": "auto", "chroma": "auto", "generative": "auto", "instructions": "",
           "attachments": [], "target_duration": None}

# Valores de estilo por tipo/estilo de edición. "max_*" limitan lo que un estilo aprendido puede pedir.
LONG = {
    "informativo": {"silence_threshold": 0.55, "zoom_amount": 1.06, "zoom_every": 3, "transition_style": "cortes",
                    "sfx_density": 0.06, "sfx_per_min": 1.2, "callouts_per_min": 0.8, "music_volume": 0.05,
                    "broll_every_s": 45},
    "tutorial": {"silence_threshold": 0.6, "zoom_amount": 1.08, "zoom_every": 3, "transition_style": "cortes",
                 "sfx_density": 0.05, "sfx_per_min": 0.8, "callouts_per_min": 1.0, "music_volume": 0.04,
                 "broll_every_s": 60},
    "podcast": {"silence_threshold": 0.8, "zoom_amount": 1.04, "zoom_every": 4, "transition_style": "cortes",
                "sfx_density": 0.0, "sfx_per_min": 0.0, "callouts_per_min": 0.4, "music_volume": 0.0,
                "broll_every_s": 0},
    "entretenimiento": {"silence_threshold": 0.45, "zoom_amount": 1.1, "zoom_every": 2, "transition_style": "suave",
                        "sfx_density": 0.14, "sfx_per_min": 3.0, "callouts_per_min": 1.5, "music_volume": 0.07,
                        "broll_every_s": 35},
    "gaming": {"silence_threshold": 0.45, "zoom_amount": 1.08, "zoom_every": 2, "transition_style": "suave",
               "sfx_density": 0.12, "sfx_per_min": 2.5, "callouts_per_min": 1.2, "music_volume": 0.06,
               "broll_every_s": 40},
    "vlog": {"silence_threshold": 0.5, "zoom_amount": 1.08, "zoom_every": 2, "transition_style": "suave",
             "sfx_density": 0.1, "sfx_per_min": 2.0, "callouts_per_min": 1.0, "music_volume": 0.08,
             "broll_every_s": 30},
    "viral": {"silence_threshold": 0.35, "zoom_amount": 1.12, "zoom_every": 1, "transition_style": "dinamico",
              "sfx_density": 0.3, "sfx_per_min": 6.0, "callouts_per_min": 3.0, "music_volume": 0.1,
              "broll_every_s": 20},
}
SHORT = {
    "informativo": {"silence_threshold": 0.35, "zoom_amount": 1.12, "zoom_every": 1, "transition_style": "dinamico",
                    "sfx_density": 0.25, "sfx_per_min": 6.0, "callouts_per_min": 3.0, "music_volume": 0.1,
                    "broll_every_s": 12},
    "viral": {"silence_threshold": 0.3, "zoom_amount": 1.15, "zoom_every": 1, "transition_style": "dinamico",
              "sfx_density": 0.4, "sfx_per_min": 10.0, "callouts_per_min": 4.0, "music_volume": 0.14,
              "broll_every_s": 8},
    "podcast": {"silence_threshold": 0.45, "zoom_amount": 1.1, "zoom_every": 1, "transition_style": "cortes",
                "sfx_density": 0.1, "sfx_per_min": 2.0, "callouts_per_min": 2.0, "music_volume": 0.06,
                "broll_every_s": 0},
}
for _k in ("entretenimiento", "gaming", "vlog", "tutorial"):
    SHORT[_k] = {**SHORT["viral"], "sfx_density": 0.3, "sfx_per_min": 7.0, "callouts_per_min": 3.0}

CAPS_LONG = {"clean": {"caption_style": "clean", "caption_max_words": 7, "caption_size": 60,
                       "caption_uppercase": False, "caption_position": "bottom"}}
CAPS_SHORT = {"karaoke": {"caption_style": "karaoke", "caption_max_words": 3, "caption_size": 88,
                          "caption_uppercase": True, "caption_position": "auto"}}


def _seconds(v) -> float | None:
    try:
        s = float(v)
    except (TypeError, ValueError):
        return None
    return round(min(max(s, 10.0), 4 * 3600.0), 1) if s > 0 else None


def normalize(b: dict | None) -> dict:
    b = {**DEFAULT, **(b or {})}
    out = {
        "video_type": b["video_type"] if b["video_type"] in VIDEO_TYPES else None,
        "edit_style": b["edit_style"] if b["edit_style"] in EDIT_STYLES else None,
        "learned_style_id": int(b["learned_style_id"]) if str(b.get("learned_style_id") or "").isdigit() else None,
        "captions": b["captions"] if b["captions"] in TRI else "auto",
        "caption_look": b["caption_look"] if b["caption_look"] in CAPTION_LOOKS else "auto",
        "music": b["music"] if b["music"] in TRI else "auto",
        "chroma": b["chroma"] if b["chroma"] in TRI else "auto",
        "generative": b.get("generative") if b.get("generative") in TRI else "auto",
        "instructions": str(b.get("instructions") or "")[:1500],
        "attachments": [],
        "target_duration": _seconds(b.get("target_duration")),  # duración final pedida (s); None = todo lo útil
    }
    seen = set()
    for a in b.get("attachments") or []:
        try:
            vid = int(a.get("video_id"))
        except (TypeError, ValueError, AttributeError):
            continue
        if vid in seen:
            continue
        seen.add(vid)
        out["attachments"].append({"video_id": vid, "role": a.get("role") if a.get("role") in ROLES else "auto",
                                   "note": str(a.get("note") or "")[:200]})
    return out


def infer(p: dict) -> dict:
    """Brief efectivo: lo que el usuario fijó y, si no, lo que se deduce del proyecto."""
    b = normalize((p.get("source_meta") or {}).get("brief"))
    dur = float((p.get("source_meta") or {}).get("duration") or 0)
    if not b["video_type"]:
        b["video_type"] = "long" if (p.get("format") == "horizontal" or dur > 180) else "short"
    if not b["edit_style"]:
        b["edit_style"] = "informativo" if b["video_type"] == "long" else "viral"
    return b


def learned_style(style_id: int | None) -> dict | None:
    if not style_id:
        return None
    try:
        from . import learn
        return learn.style_for(style_id)
    except Exception:  # noqa: BLE001 — estilo borrado
        return None


def style_for(p: dict, b: dict) -> tuple[dict, dict]:
    """Estilo final para la auto-edición + parámetros de ritmo.

    Con estilo aprendido: manda su look (colores, fuentes, transiciones, tipos de sonido) y su ritmo, pero en
    videos largos se limita a lo que tiene sentido (no 10 efectos por minuto en un video de 8 min)."""
    base = dict(p.get("style") or {})
    preset = (LONG if b["video_type"] == "long" else SHORT).get(b["edit_style"]) or \
        (LONG if b["video_type"] == "long" else SHORT)["informativo" if b["video_type"] == "long" else "viral"]
    pace = {"broll_every_s": preset["broll_every_s"]}
    learned = learned_style(b["learned_style_id"])
    keys = ("silence_threshold", "zoom_amount", "zoom_every", "transition_style", "sfx_density", "sfx_per_min",
            "callouts_per_min", "music_volume")
    if learned:
        st = {**base, **learned}
        measured = st.get("zoom_kind") in ("punch", "ramp", "none")  # zooms medidos en la referencia: se respetan
        if b["video_type"] == "long":
            for k in ("sfx_density", "sfx_per_min", "callouts_per_min", "music_volume") + (() if measured else ("zoom_amount",)):
                st[k] = min(float(st.get(k) or 0), float(preset[k]) * 1.6) if k != "zoom_amount" else \
                    min(float(st.get(k) or 1), preset["zoom_amount"] + 0.04)
            if not measured:
                st["zoom_every"] = max(int(st.get("zoom_every") or 1), preset["zoom_every"] - 1)
            if st.get("transition_style") == "dinamico":
                st["transition_style"] = "suave"
            st["silence_threshold"] = max(float(st.get("silence_threshold") or 0), preset["silence_threshold"] - 0.1)
    else:
        st = {**base, **{k: preset[k] for k in keys}}
    # subtítulos
    look = b["caption_look"]
    if look == "auto":
        look = "clean" if b["video_type"] == "long" else (st.get("caption_style") if learned and
                                                           st.get("caption_style") not in (None, "none") else "karaoke")
    if look == "clean":
        st.update(CAPS_LONG["clean"])
    elif look == "karaoke" and not learned:
        st.update(CAPS_SHORT["karaoke"])
    elif look in ("karaoke", "simple"):
        st["caption_style"] = look
    captions_on = b["captions"] == "on" or (b["captions"] == "auto" and b["video_type"] == "short")
    if b["music"] == "off":
        st["music_volume"] = 0.0
    elif b["music"] == "on" and float(st.get("music_volume") or 0) < 0.04:
        st["music_volume"] = 0.06 if b["video_type"] == "long" else 0.12
    pace["captions_on"] = captions_on
    serious = b["edit_style"] in ("informativo", "tutorial", "podcast")
    if b["video_type"] == "long":
        # Videos largos: algo cambia cada 15-30 s, no cada 5. En los serios, nada de memes ni sacudidas.
        st["memes"] = not serious
        st["fx_gap_s"] = 15.0 if serious else 8.0
    else:
        st["memes"] = b["edit_style"] not in ("informativo", "tutorial") or not serious
        st["fx_gap_s"] = 0.0
    return style_mod.clean_style(st), pace


def save(pid: int, b: dict) -> dict:
    import json
    b = normalize(b)
    with db.get_conn() as conn:
        r = conn.execute("SELECT source_meta FROM studio_projects WHERE id = ?", (pid,)).fetchone()
        meta = json.loads(r["source_meta"] or "{}") if r else {}
        meta["brief"] = b
        conn.execute("UPDATE studio_projects SET source_meta = ? WHERE id = ?", (json.dumps(meta), pid))
    return b


def attachments_for_llm(b: dict, max_chars: int = 2500) -> tuple[str, list[dict]]:
    """Videos adjuntos para la IA: id, título, duración, rol y de qué hablan (si tienen transcripción)."""
    import json
    if not b["attachments"]:
        return "", []
    rows, parts = [], []
    with db.get_conn() as conn:
        for a in b["attachments"]:
            v = conn.execute("SELECT id, title, duration, stored_path FROM videos WHERE id = ?", (a["video_id"],)).fetchone()
            if not v:
                continue
            t = conn.execute("SELECT segments FROM transcripts WHERE video_id = ?", (v["id"],)).fetchone()
            gist = ""
            if t:
                segs = json.loads(t["segments"] or "[]")
                gist = " ".join(s.get("text", "") for s in segs[:40])[:300]
            rows.append({**a, "title": v["title"], "duration": v["duration"] or 0})
            parts.append(f"video_id={v['id']} «{v['title']}» ({(v['duration'] or 0) / 60:.0f} min, rol: {a['role']})"
                         + (f" nota: {a['note']}" if a["note"] else "") + (f" — dice: {gist}" if gist else ""))
    return "\n".join(parts)[:max_chars], rows
