"""Video desde un tema: guion (qwen3) → tu voz clonada con emociones → tomas de tus clips → timeline editable."""
from __future__ import annotations

import json
import shutil
import time
import uuid
from pathlib import Path

from .. import config, db
from ..ai import llm, transcribe
from . import assets, footage, projects, voice
from . import style as style_mod
from . import timeline as tlm
from .probe import probe_any

EMO_LIST = list(voice.EMOTIONS)

SCRIPT_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "music_mood": {"type": "string", "enum": ["upbeat", "electronic", "epic", "comedy", "chill", "scoring", "horror"]},
        "lines": {"type": "array", "items": {"type": "object", "properties": {
            "text": {"type": "string"},
            "emotion": {"type": "string", "enum": EMO_LIST},
            "on_screen": {"type": "string"},
            "shot_ids": {"type": "array", "items": {"type": "integer"}},
            "sfx": {"type": "string", "enum": ["", "impacto", "whoosh", "pop", "ding", "riser", "bassdrop", "glitch",
                                               "latido", "tictac", "error", "camara"]},
        }, "required": ["text", "emotion", "on_screen", "shot_ids", "sfx"]}},
    },
    "required": ["title", "music_mood", "lines"],
}

SCRIPT_SYSTEM = """Eres guionista y editor de videos cortos virales en español (TikTok, Reels, Shorts) con estilo propio:
directo, con humor ligero cuando encaja, cero relleno y frases cortas que se dicen en voz alta.
Escribes el guion que el CREADOR narrará con su propia voz, y eliges las tomas de SUS clips que lo ilustran.

Reglas del guion:
- La primera línea es el GANCHO: una pregunta, dato sorprendente o afirmación fuerte. Nada de "hola, en este video".
- EXACTAMENTE {nlines} líneas (≈{secs} s en total, unas {words} palabras). Cada línea: una frase de 8 a 16 palabras,
  salvo el gancho y el cierre, que pueden ser más cortos. No te quedes corto: el video debe durar ≈{secs} s.
- Cada línea lleva una emoción para la voz: {emos}. Varía las emociones como lo haría una persona real
  (sorpresa en el gancho, seriedad para datos, entusiasmo para el cierre, enojo cuando algo indigna, etc.).
- Usa signos ¡! y ¿? cuando correspondan: ayudan a la entonación.
- Cierra con una llamada a la acción breve y natural (comentar, seguir, guardar).
- on_screen: texto corto para pantalla (máx. 4 palabras, cifras o palabras clave) o "" si no aplica.
- sfx: efecto de sonido para esa línea ("" si no hace falta; úsalo con moderación).
- shot_ids: 1 a 3 ids del CATÁLOGO DE TOMAS que mejor ilustran esa línea (no repitas la misma toma seguido).
- title: título gancho del video (máx. 7 palabras).
- music_mood: el ambiente musical.
No inventes datos dudosos; si el tema requiere cifras que no conoces, habla en términos generales."""


def _pick_music(mood: str) -> dict | None:
    music = [a for a in assets.list_assets("music") if a["available"]]
    if not music:
        return None
    for a in music:
        if mood in a["tags"].split() or mood in a["path"].lower():
            return a
    return music[0]


SFX_TAGS = {"impacto": "impacto", "whoosh": "whoosh", "pop": "pop", "ding": "ding", "riser": "riser",
            "bassdrop": "bassdrop", "glitch": "glitch", "latido": "latido", "tictac": "reloj", "error": "error",
            "camara": "camara"}


def run_topic(job: dict, progress) -> dict:
    prm = job["params"]
    topic = prm["topic"].strip()
    secs = int(prm.get("duration", 45))
    fmt = prm.get("format", "vertical")
    st = style_mod.clean_style(prm.get("style") or db.get_setting("studio_default_style") or {})
    url, model = db.get_setting("ollama_url"), db.role_model("writer")
    assets.ensure_starter_pack()
    assets.ensure_project_media()

    # 1) Guion
    shots = footage.catalog(prm.get("video_ids") or None)
    shot_by_id = {s["id"]: s for s in shots}
    cat = [{"id": s["id"], "video": s["video_title"][:30], "seg": round(s["end_s"] - s["start_s"], 1),
            "desc": s["caption"][:140], "tags": s["tags"], "accion": s["action"]} for s in shots]
    progress(0.03, "Escribiendo el guion con IA")
    nlines = max(4, round(secs / 3.3))
    system = (SCRIPT_SYSTEM.replace("{words}", str(int(secs * 2.6))).replace("{secs}", str(secs))
              .replace("{nlines}", str(nlines))
              .replace("{emos}", ", ".join(EMO_LIST)))
    user = (f"TEMA: {topic}\n" + (f"INDICACIONES DEL CREADOR: {prm['notes']}\n" if prm.get("notes") else "")
            + f"\nCATÁLOGO DE TOMAS (id, video, duración, descripción, etiquetas, acción): "
            + (json.dumps(cat, ensure_ascii=False) if cat else "(sin tomas indexadas: deja shot_ids vacío)"))
    script = llm.chat_json(url, model, system, user, SCRIPT_SCHEMA, num_ctx=24576, keep_alive="0s")
    lines = [ln for ln in script.get("lines", []) if str(ln.get("text", "")).strip()]
    if not lines:
        raise RuntimeError("La IA no devolvió un guion válido. Intenta de nuevo o reformula el tema.")

    # 2) Voz
    work = config.WORK_DIR / f"topic_{job['id']}"
    narr, timings = voice.synthesize(lines, work, lambda p, s: progress(0.12 + 0.55 * p, s),
                                     use_profile=prm.get("use_voice", True))
    dest = config.STUDIO_MEDIA_DIR / f"narracion_{uuid.uuid4().hex[:8]}.wav"
    shutil.copy2(narr, dest)

    # 3) Tiempos por palabra para subtítulos
    progress(0.70, "Sincronizando subtítulos")
    meta = probe_any(dest)
    wav16 = work / "n16.wav"
    from ..ai import audio
    audio.extract_wav(dest, wav16)
    tr = transcribe.transcribe(wav16, db.get_setting("whisper_model"), "es", meta["duration"])
    shutil.rmtree(work, ignore_errors=True)

    # 4) Timeline
    progress(0.85, "Armando el timeline con tus clips")
    tl = tlm.empty(fmt)
    tl["tracks"]["video"] = [{"id": tlm.nid("v"), "src_in": 0.0, "src_out": meta["duration"], "start": 0.0,
                              "zoom": 1.0, "crop_x": 0.5}]
    transitions = {"dinamico": ["flash", "zoom", "slide", "glitch", "zoom"], "suave": ["fade"],
                   "cortes": ["cut"]}.get(st.get("transition_style", "dinamico"), ["cut"])
    used: list[int] = []
    k = 0
    for i, (ln, tm) in enumerate(zip(lines, timings)):
        a = tm["start"]
        b = timings[i + 1]["start"] if i + 1 < len(timings) else meta["duration"]
        ids = [s for s in ln.get("shot_ids", []) if s in shot_by_id][:3]
        if not ids and shots:  # sin elección válida: tomas con más acción aún no usadas
            pool = sorted([s for s in shots if s["id"] not in used[-6:]], key=lambda s: -(s["action"] + s["motion"]))
            ids = [pool[i % len(pool)]["id"]] if pool else []
        if ids:
            part = (b - a) / len(ids)
            for j, sid in enumerate(ids):
                s = shot_by_id[sid]
                length = part
                src_in = s["start_s"] + max(0.0, (s["end_s"] - s["start_s"] - length) / 2)
                tl["tracks"]["broll"].append({
                    "id": tlm.nid("b"), "video_id": s["video_id"], "start": round(a + j * part, 3),
                    "end": round(a + (j + 1) * part, 3), "src_in": round(max(0.0, src_in), 3),
                    "transition": transitions[k % len(transitions)] if k else "cut",
                    "volume": st.get("broll_volume", 0.0), "fit": st.get("broll_fit", "cover")})
                used.append(sid)
                k += 1
        if ln.get("on_screen"):
            tl["tracks"]["text"].append({"id": tlm.nid("t"), "start": a, "end": min(b, a + 2.2),
                                         "text": ln["on_screen"][:60], "kind": "callout"})
        tag = SFX_TAGS.get(ln.get("sfx") or "")
        if tag:
            sfx = assets.find_by_tag("sfx", tag)
            if sfx:
                tl["tracks"]["sfx"].append({"id": tlm.nid("s"), "asset_id": sfx["id"], "start": max(0.0, a - 0.05),
                                            "volume": 0.55})
    title = str(script.get("title", "")).strip()
    if title:
        tl["tracks"]["text"].insert(0, {"id": tlm.nid("t"), "start": 0.0, "end": min(2.6, meta["duration"]),
                                        "text": title[:70], "kind": "title"})
        tl["tracks"]["text"] = [t for t in tl["tracks"]["text"] if t["kind"] == "title" or t["start"] >= 2.4]
    mus = _pick_music(script.get("music_mood", "upbeat"))
    if mus:
        tl["tracks"]["music"].append({"id": tlm.nid("m"), "asset_id": mus["id"], "start": 0.0,
                                      "end": meta["duration"], "volume": st["music_volume"]})

    with db.get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO studio_projects (name, format, source_path, source_kind, source_meta, transcript, timeline, style) "
            "VALUES (?, ?, ?, 'audio', ?, ?, ?, ?)",
            ((prm.get("name") or title or topic)[:120], fmt, str(dest), json.dumps(meta),
             json.dumps(tr, ensure_ascii=False), json.dumps(tl, ensure_ascii=False), json.dumps(st)))
        pid = cur.lastrowid
    projects.save(pid, note="Video desde un tema")
    with db.get_conn() as conn:
        conn.execute("UPDATE studio_projects SET source_meta = ? WHERE id = ?",
                     (json.dumps({**meta, "script": lines, "topic": topic}, ensure_ascii=False), pid))
    return {"project_id": pid, "lines": len(lines), "duration": meta["duration"], "shots": k,
            "voice": "clonada" if (prm.get("use_voice", True) and voice.profile()) else "por defecto"}
