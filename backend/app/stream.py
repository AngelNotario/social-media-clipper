"""Modo stream: grabaciones de OBS (Twitch/YouTube Live).

- Cámara (facecam): se detecta sola o la marcas tú. Modo híbrido: en cada clip se comprueba si la cámara
  estaba encendida; si no, el clip usa el juego completo con fondo desenfocado.
- Clips verticales: cámara arriba + juego abajo.
- Pistas de audio de OBS: si grabaste el micrófono aparte, se transcribe solo tu voz.
- Video resumen para YouTube: los mejores momentos del stream en orden, con gancho inicial y capítulos.
"""
from __future__ import annotations

import json
import statistics
import subprocess
from pathlib import Path

import numpy as np

from . import config, db

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS video_settings (
    video_id    INTEGER PRIMARY KEY REFERENCES videos(id) ON DELETE CASCADE,
    facecam     TEXT,
    cam_mode    TEXT NOT NULL DEFAULT 'auto',
    voice_track INTEGER
);
"""

CAM_H, GAME_H, OUT_W = 680, 1240, 1080   # lienzo vertical 1080x1920: cámara arriba, juego abajo


def ensure_schema() -> None:
    with db.get_conn() as conn:
        conn.executescript(SCHEMA_SQL)


def settings(video_id: int) -> dict:
    ensure_schema()
    with db.get_conn() as conn:
        r = conn.execute("SELECT * FROM video_settings WHERE video_id = ?", (video_id,)).fetchone()
    if not r:
        return {"facecam": None, "cam_mode": "auto", "voice_track": None}
    return {"facecam": json.loads(r["facecam"]) if r["facecam"] else None, "cam_mode": r["cam_mode"],
            "voice_track": r["voice_track"]}


def save_settings(video_id: int, **fields) -> dict:
    ensure_schema()
    cur = settings(video_id)
    cur.update({k: v for k, v in fields.items() if k in cur})
    with db.get_conn() as conn:
        conn.execute("INSERT OR REPLACE INTO video_settings (video_id, facecam, cam_mode, voice_track) VALUES (?, ?, ?, ?)",
                     (video_id, json.dumps(cur["facecam"]) if cur["facecam"] else None, cur["cam_mode"],
                      cur["voice_track"]))
    return cur


def voice_track(video_id: int, src: Path) -> int | None:
    """Pista con tu micrófono. Por defecto: la 2.ª pista si OBS grabó varias (pista 1 = mezcla)."""
    from .ai.audio import audio_tracks
    st = settings(video_id)
    n = audio_tracks(src)
    if st["voice_track"] is not None:
        return st["voice_track"] if 0 < st["voice_track"] < n else None
    return 1 if n >= 2 else None


# ---------------- cámara ----------------

def _frames(src: Path, times: list[float], width: int = 960):
    import cv2
    import numpy as np
    out = []
    for t in times:
        r = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{t:.2f}", "-i", str(src), "-frames:v", "1",
                            "-vf", f"scale={width}:-2", "-f", "image2pipe", "-vcodec", "png", "-"],
                           capture_output=True, timeout=60)
        if r.stdout:
            img = cv2.imdecode(np.frombuffer(r.stdout, np.uint8), cv2.IMREAD_COLOR)
            if img is not None:
                out.append((t, img))
    return out


def detect_facecam(src: Path, duration: float, samples: int = 14) -> dict | None:
    """Busca un rostro pequeño y fijo (recuadro de cámara). Devuelve {x,y,w,h} en fracciones o None."""
    from .editor.crop import _detector
    detect = _detector()
    if detect is None or not duration:
        return None
    times = [duration * (i + 0.5) / samples for i in range(samples)]
    boxes = []
    for _, img in _frames(src, times):
        h, w = img.shape[:2]
        faces = detect(img)
        for f in faces:
            fx, fy, fw, fh = (float(f[0]) / w, float(f[1]) / h, float(f[2]) / w, float(f[3]) / h)
            if fw < 0.22:  # un rostro de cámara de stream ocupa poco de la pantalla
                boxes.append((fx + fw / 2, fy + fh / 2, fw, fh))
    if len(boxes) < max(3, samples // 4):
        return None
    cx = statistics.median(b[0] for b in boxes)
    cy = statistics.median(b[1] for b in boxes)
    near = [b for b in boxes if abs(b[0] - cx) < 0.06 and abs(b[1] - cy) < 0.08]
    if len(near) < max(3, samples // 4):
        return None
    fw = statistics.median(b[2] for b in near)
    fh = statistics.median(b[3] for b in near)
    # recuadro ≈ 3 veces el rostro, con la proporción del panel superior (1080x680)
    rw = min(0.6, fw * 3.2)
    rh = min(0.6, rw * (16 / 9) * (CAM_H / OUT_W))
    x = min(max(cx - rw / 2, 0.0), 1 - rw)
    y = min(max(cy - rh * 0.45, 0.0), 1 - rh)
    return {"x": round(float(x), 4), "y": round(float(y), 4), "w": round(float(rw), 4), "h": round(float(rh), 4)}


def camera_active(src: Path, rect: dict, start: float, end: float, samples: int = 6) -> bool:
    """¿Se ve un rostro dentro del recuadro de cámara durante este tramo?"""
    from .editor.crop import _detector
    detect = _detector()
    if detect is None:
        return True
    hits = 0
    times = [start + (end - start) * (i + 0.5) / samples for i in range(samples)]
    for _, img in _frames(src, times, width=1280):
        h, w = img.shape[:2]
        x0, y0 = int(rect["x"] * w), int(rect["y"] * h)
        x1, y1 = int((rect["x"] + rect["w"]) * w), int((rect["y"] + rect["h"]) * h)
        roi = img[y0:y1, x0:x1]
        if roi.size and detect(roi):
            hits += 1
    return hits >= 1


def split_filter(sw: int, sh: int, rect: dict, game_center: float = 0.5) -> str:
    """Filtro FFmpeg: cámara arriba (1080x680) + juego abajo (1080x1240) → [v0]."""
    fx, fy = int(rect["x"] * sw), int(rect["y"] * sh)
    fw, fh = max(2, int(rect["w"] * sw)) // 2 * 2, max(2, int(rect["h"] * sh)) // 2 * 2
    gh = sh // 2 * 2
    gw = min(sw, int(sh * OUT_W / GAME_H)) // 2 * 2
    gx = int(min(max(game_center * sw - gw / 2, 0), sw - gw))
    return (f"[0:v]split[cs][gs];"
            f"[cs]crop={fw}:{fh}:{fx}:{fy},scale={OUT_W}:{CAM_H}:force_original_aspect_ratio=increase,"
            f"crop={OUT_W}:{CAM_H},setsar=1[cam];"
            f"[gs]crop={gw}:{gh}:{gx}:0,scale={OUT_W}:{GAME_H},setsar=1[game];"
            f"[cam][game]vstack,setsar=1[v0]")


def stream_layout(video: dict, start: float, end: float) -> tuple[str, str]:
    """Elige el encuadre para un clip de stream. Devuelve (filtro, modo_usado)."""
    from .editor.render import video_filter
    st = settings(video["id"])
    rect = st["facecam"]
    src = Path(video["stored_path"])
    use_cam = bool(rect) and st["cam_mode"] != "never" and (
        st["cam_mode"] == "always" or camera_active(src, rect, start, end))
    if use_cam:
        return split_filter(video["width"], video["height"], rect), "stream (cámara + juego)"
    return video_filter("blur", video["width"], video["height"], None), "stream sin cámara (juego completo)"


# ---------------- resumen para YouTube ----------------

BEAT_SCHEMA = {
    "type": "object",
    "properties": {"beats": {"type": "array", "items": {"type": "object", "properties": {
        "id": {"type": "integer"}, "score": {"type": "integer"}, "chapter": {"type": "string"}},
        "required": ["id", "score", "chapter"]}}},
    "required": ["beats"],
}
BEAT_SYSTEM = """Eres editor de un canal de gaming en YouTube. Recibes bloques de un stream con lo que dijo el streamer
(transcripción) y su nivel de emoción en el audio (0-100). Califica cada bloque de 0 a 100 según qué tan
entretenido sería en un video resumen: momentos graciosos, épicos, tensos, fails, logros, reacciones fuertes o
explicaciones interesantes valen mucho; esperas, menús, silencios, problemas técnicos y charla sin contenido valen poco.
chapter: título corto (2-5 palabras) del capítulo al que pertenece el bloque (bloques seguidos del mismo tema
comparten título). Responde solo JSON."""

META_SCHEMA = {
    "type": "object",
    "properties": {"titles": {"type": "array", "items": {"type": "string"}},
                   "description": {"type": "string"}, "tags": {"type": "array", "items": {"type": "string"}},
                   "thumbnail_text": {"type": "array", "items": {"type": "string"}}},
    "required": ["titles", "description", "tags", "thumbnail_text"],
}
META_SYSTEM = """Eres experto en YouTube Gaming en español. Con los capítulos y lo que pasó en el video, propone:
titles: 3 títulos con gancho (máx. 70 caracteres, sin clickbait engañoso);
description: 2-3 frases que resuman el video (sin los capítulos, se añaden aparte);
tags: 10-15 etiquetas; thumbnail_text: 3 textos de 2-4 palabras para la miniatura. Responde solo JSON."""


def _beats(segments: list[dict], duration: float, energy: list[float]) -> list[dict]:
    """Divide el stream en bloques de ~45-90 s cortando en pausas de la voz."""
    from .ai.highlights import energy_score
    beats, cur_start, last_end = [], 0.0, 0.0
    for s in segments:
        if s["start"] - cur_start >= 45 and (s["start"] - last_end > 1.0 or s["start"] - cur_start >= 90):
            beats.append((cur_start, s["start"]))
            cur_start = s["start"]
        last_end = s["end"]
    t = cur_start
    while duration - t > 0:
        step = min(75.0, duration - t)
        if duration - t < 100:
            step = duration - t
        beats.append((t, t + step))
        t += step
    out = []
    for i, (a, b) in enumerate(beats):
        if b - a < 8:
            continue
        text = " ".join(s["text"] for s in segments if s["end"] > a and s["start"] < b)
        out.append({"id": i, "start": round(a, 2), "end": round(b, 2), "text": text,
                    "energy": round(energy_score(a, b, energy), 1)})
    return out


def _norm_words(text: str) -> set[str]:
    import unicodedata
    t = unicodedata.normalize("NFKD", text.lower())
    t = "".join(c for c in t if not unicodedata.combining(c))
    return {w for w in "".join(c if c.isalnum() else " " for c in t).split() if len(w) > 2}


def _same_chapter(a: str | None, b: str | None) -> bool:
    """«Lucha épica» y «Lucha Épica» (o «Pelea épica») son el mismo capítulo."""
    if not a or not b:
        return False
    wa, wb = _norm_words(a), _norm_words(b)
    return bool(wa and wb) and len(wa & wb) / len(wa | wb) >= 0.5


def _looks_dark(src: Path, span: tuple[float, float]) -> bool:
    """¿El tramo es casi negro (carga, muerte, transición del juego)?"""
    import cv2
    shots = _frames(src, [span[0] + 0.5, (span[0] + span[1]) / 2, span[1] - 0.5], width=320)
    dark = sum(1 for _, img in shots if float(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY).mean()) < 38)
    return not shots or dark >= 2


def _best_frame(src: Path, around: float, lo: float, hi: float) -> float:
    """Entre varios cuadros cerca del pico, el más nítido y con buena luz (evita pantallas negras o de carga)."""
    import cv2
    times = [min(max(around + d, lo), hi) for d in (-4, -2.5, -1, 0, 1, 2.5, 4, 6)]
    best, best_score = around, -1.0
    for t, img in _frames(src, sorted(set(times)), width=480):
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        mean, std = float(gray.mean()), float(gray.std())
        sharp = float(cv2.Laplacian(gray, cv2.CV_64F).var())
        score = (std * min(sharp, 2000) ** 0.5) * (1.0 if 45 < mean < 210 else 0.15)
        if score > best_score:
            best, best_score = t, score
    return best


VIRAL = {"zoom_amount": 1.1, "zoom_every": 2, "sfx_density": 0.45, "callouts_per_min": 4,
         "transition_style": "dinamico", "caption_style": "karaoke", "caption_max_words": 4, "caption_size": 64,
         "caption_uppercase": True, "silence_threshold": 0.4, "music_volume": 0.0}


def viral_style(learned: dict | None, horizontal: bool = True) -> dict:
    """Estilo para el resumen: el aprendido si lo eliges (ajustado a horizontal) o el viral predeterminado."""
    from .studio import style as style_mod
    base = learned or {**(db.get_setting("studio_default_style") or {}), **VIRAL}
    st = style_mod.clean_style(base)
    if horizontal:  # en 16:9 los subtítulos gigantes de los shorts tapan el juego
        st["caption_size"] = min(st["caption_size"], 66)
        st["caption_max_words"] = max(st["caption_max_words"], 3)
    return st


def learn_name(style_id) -> str | None:
    if not style_id:
        return None
    from .studio import learn
    learn.ensure_schema()
    with db.get_conn() as conn:
        r = conn.execute("SELECT name FROM learned_styles WHERE id = ?", (style_id,)).fetchone()
    return r["name"] if r else None


def _ts(sec: float) -> str:
    sec = int(sec)
    h, m, s = sec // 3600, (sec % 3600) // 60, sec % 60
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def run_youtube(job: dict, progress) -> dict:
    from .ai import audio, llm, transcribe
    from .ai.pipeline import load_transcript
    from . import knowledge
    from .studio import director, learn, projects
    from .studio import timeline as tlm

    prm = job["params"]
    with db.get_conn() as conn:
        v = conn.execute("SELECT * FROM videos WHERE id = ?", (prm["video_id"],)).fetchone()
    if not v:
        raise RuntimeError("Video no encontrado.")
    v = dict(v)
    src = Path(v["stored_path"])
    dur = v["duration"] or 0
    target = float(prm.get("target_min", 17)) * 60
    url, model = db.get_setting("ollama_url"), db.get_setting("ollama_model")
    style_id = prm.get("style_id")
    st = viral_style(learn.style_for(style_id) if style_id else None, horizontal=True)
    if prm.get("captions") is False:
        st["caption_style"] = "none"
    context = knowledge.prompt_context("youtube", style_id)

    # 1) Transcripción (de tu micrófono si OBS lo grabó aparte) y energía
    wav = config.WORK_DIR / f"yt_{v['id']}.wav"
    vwav = config.WORK_DIR / f"yt_{v['id']}_voz.wav"
    vt = voice_track(v["id"], src)
    try:
        progress(0.01, "Extrayendo audio del stream")
        audio.extract_wav(src, wav)
        if vt:
            audio.extract_wav(src, vwav, vt)
        tr = load_transcript(v["id"])
        if tr is None:
            res = transcribe.transcribe(vwav if vt else wav, db.get_setting("whisper_model"),
                                        db.get_setting("whisper_language"), dur,
                                        lambda p: progress(0.03 + 0.4 * p, f"Transcribiendo el stream ({int(p * 100)}%)"))
            with db.get_conn() as conn:
                conn.execute("INSERT OR REPLACE INTO transcripts (video_id, language, model, device, segments) "
                             "VALUES (?, ?, ?, ?, ?)", (v["id"], res["language"], res["model"], res["device"],
                                                        json.dumps(res["segments"], ensure_ascii=False)))
            tr = load_transcript(v["id"])
        progress(0.45, "Midiendo la emoción del stream")
        energy = audio.energy_curve(wav)
        voice_energy = audio.energy_curve(vwav) if vt and vwav.exists() else None
    finally:
        wav.unlink(missing_ok=True)
        vwav.unlink(missing_ok=True)
    segments = tr["segments"]

    # 2) Bloques calificados por la IA (+ energía)
    beats = _beats(segments, dur, energy)
    if not beats:
        raise RuntimeError("El video es demasiado corto para un resumen.")
    ls = llm.status(url)
    ai_ok = ls["available"] and model in ls["models"]
    rating = {}
    if ai_ok:
        chunk = 10
        for n in range(0, len(beats), chunk):
            part = beats[n:n + chunk]
            progress(0.47 + 0.35 * n / len(beats), f"La IA está viendo el stream ({n + len(part)}/{len(beats)} bloques)")
            data = [{"id": b["id"], "inicio": _ts(b["start"]), "emocion_audio": b["energy"],
                     "transcripcion": b["text"][:420] or "(sin voz)"} for b in part]
            try:
                r = llm.chat_json(url, model, BEAT_SYSTEM + "\n\n" + context[:2600], json.dumps(data, ensure_ascii=False), BEAT_SCHEMA,
                                  num_ctx=12288, max_tokens=900)
                for x in r.get("beats", []):
                    rating[int(x["id"])] = x
            except llm.LLMError:
                continue
    for b in beats:
        r = rating.get(b["id"])
        ai = max(0, min(100, int(r["score"]))) if r else None
        b["chapter"] = (r or {}).get("chapter") or ""
        b["score"] = round(0.7 * ai + 0.3 * b["energy"], 1) if ai is not None else b["energy"]

    # 3) Selección hasta la duración objetivo (medida DESPUÉS de quitar tiempos muertos), en orden cronológico
    words = [w for s in segments for w in (s.get("words") or [])]
    gap_thr = max(0.9, min(3.0, st["silence_threshold"] * 5))
    low_e = float(np.percentile(energy, 45)) if energy else 0.3
    chosen, total = [], 0.0
    for b in sorted(beats, key=lambda x: -x["score"]):
        if total >= target:
            break
        b["keep"] = director.jump_cut_ranges(b["start"], b["end"], words, energy, gap_thr, low_e)
        chosen.append(b)
        total += sum(y - x for x, y in b["keep"])
    chosen.sort(key=lambda x: x["start"])
    merged: list[dict] = []
    for b in chosen:
        if merged and b["start"] - merged[-1]["end"] < 3:
            merged[-1]["end"] = b["end"]
            merged[-1]["score"] = max(merged[-1]["score"], b["score"])
            merged[-1]["keep"] = merged[-1]["keep"] + b["keep"]
        else:
            merged.append(dict(b))

    # 4) Gancho inicial: el pico de emoción de los mejores bloques (≈10 s), evitando pantallas negras o de carga
    hop = audio.ENERGY_HOP
    cold = (0.0, 0.0)
    for best in sorted(merged, key=lambda x: -x["score"])[:5]:
        a_i, b_i = int(best["start"] / hop), max(int(best["end"] / hop), int(best["start"] / hop) + 1)
        window = energy[a_i:b_i] or [0]
        peak_t = (a_i + window.index(max(window))) * hop
        cand = (max(best["start"], peak_t - 6), min(best["end"], peak_t + 4))
        if cand[1] - cand[0] >= 4 and not _looks_dark(src, cand):
            cold = cand
            break

    progress(0.84, "Armando la edición (cortes, ritmo y estilo)")
    tl = tlm.empty("horizontal")
    segs = []
    if prm.get("cold_open", True) and cold[1] - cold[0] >= 4:
        segs.append({"id": tlm.nid("v"), "src_in": cold[0], "src_out": cold[1], "zoom": 1.0, "crop_x": 0.5,
                     "scene_start": True, "cold": True})
    for b in merged:
        for k, (x, y) in enumerate(b["keep"]):
            segs.append({"id": tlm.nid("v"), "src_in": x, "src_out": y, "zoom": 1.0, "crop_x": 0.5,
                         "scene_start": k == 0, "chapter": (b.get("chapter") or "") if k == 0 else ""})
    tl["tracks"]["video"] = segs
    tlm.relayout(tl, words, dur)
    if st["caption_style"] == "none":
        tl["captions"]["enabled"] = False
    director.stylize(tl, st)
    if tl["tracks"]["video"] and tl["tracks"]["video"][0].get("cold"):
        tl["tracks"]["video"][0]["fx"] = [{"t": 0.15, "type": "punch", "amount": 0.14}]

    # Gancho: texto grande sobre el inicio
    if cold[1] - cold[0] >= 4 and prm.get("cold_open", True):
        hook = (rating.get(max(merged, key=lambda x: x["score"])["id"]) or {}).get("chapter") or "NO ESPERABA ESTO"
        tl["tracks"]["text"].append({"id": tlm.nid("t"), "start": 0.4, "end": 2.6, "text": hook.upper()[:30], "kind": "meme"})

    chapters, last = [], None
    for s in tl["tracks"]["video"]:
        ch = s.get("chapter")
        if ch and not _same_chapter(ch, last) and (not chapters or s["start"] - chapters[-1][0] >= 40):
            chapters.append((s["start"], ch))
            tl["tracks"]["text"].append({"id": tlm.nid("t"), "start": s["start"] + 0.3,
                                         "end": min(tl["duration"], s["start"] + 4.3), "text": ch[:50], "kind": "lower"})
            last = ch

    # El director: golpes de zoom, sacudidas, memes, efectos y zoom a tu cámara
    cam = settings(v["id"])
    cam_fn = None
    if cam["facecam"] and cam["cam_mode"] != "never":
        def cam_fn(t: float, _r=cam["facecam"], _mode=cam["cam_mode"]):
            for x in tl["tracks"]["video"]:
                if x["start"] <= t < x["start"] + (x["src_out"] - x["src_in"]):
                    s0 = x["src_in"] + (t - x["start"])
                    return _r if _mode == "always" or camera_active(src, _r, s0, s0 + 1.5, samples=3) else None
            return None
    curve = director.timeline_curve(tl, energy)
    vcurve = director.timeline_curve(tl, voice_energy) if voice_energy else None
    dstats = director.direct(tl, st, curve, vcurve, context, cam_fn,
                             lambda x, m: progress(0.86 + 0.08 * x, m))
    if director.music_bed(tl, st) and st.get("beat_sync", True):
        try:  # zooms, textos y efectos al ritmo de la música
            dstats["beats"] = director.apply_beats(tl)
        except Exception as exc:  # noqa: BLE001
            dstats["beats"] = {"error": str(exc)[:160]}
    tlm.relayout(tl, words, dur)

    if not chapters or chapters[0][0] > 1:
        chapters.insert(0, (0.0, "Intro"))
    if prm.get("cold_open", True) and len(chapters) > 1:
        chapters[0] = (0.0, "Lo mejor del stream")

    # 5) Título, descripción, etiquetas y miniaturas
    meta = {"titles": [], "description": "", "tags": [], "thumbnail_text": []}
    if ai_ok:
        try:
            summary = [{"capitulo": c, "inicio": _ts(t)} for t, c in chapters]
            excerpt = " ".join(b["text"][:200] for b in merged[:12])
            meta = llm.chat_json(url, db.role_model("writer"), META_SYSTEM,
                                 json.dumps({"capitulos": summary, "lo_que_pasa": excerpt[:3000]}, ensure_ascii=False),
                                 META_SCHEMA, num_ctx=8192, max_tokens=900, keep_alive="0s")
        except llm.LLMError:
            pass
    chapter_lines = "\n".join(f"{_ts(t)} {c}" for t, c in chapters)
    description = (meta.get("description", "").strip() + "\n\nCapítulos:\n" + chapter_lines).strip()

    thumbs_dir = config.STUDIO_DIR / "thumbs"
    thumbs_dir.mkdir(parents=True, exist_ok=True)
    thumbs = []
    for k, b in enumerate(sorted(merged, key=lambda x: -x["score"])[:4]):
        a_i, b_i = int(b["start"] / hop), max(int(b["end"] / hop), int(b["start"] / hop) + 1)
        w = energy[a_i:b_i] or [0]
        t = _best_frame(src, (a_i + w.index(max(w))) * hop, b["start"], b["end"])
        out = thumbs_dir / f"video{v['id']}_{job['id']}_{k}.jpg"
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", f"{t:.2f}", "-i", str(src), "-frames:v", "1",
                        "-vf", "scale=1280:720:force_original_aspect_ratio=increase,crop=1280:720", "-q:v", "3", str(out)],
                       capture_output=True)
        if out.exists():
            thumbs.append(out.name)

    with db.get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO studio_projects (name, format, source_path, source_kind, source_meta, transcript, timeline, style) "
            "VALUES (?, 'horizontal', ?, 'video', ?, ?, ?, ?)",
            (f"YouTube: {(meta.get('titles') or [v['title']])[0]}"[:120], str(src),
             json.dumps({"duration": dur, "width": v["width"], "height": v["height"], "has_video": True,
                         "has_audio": bool(v["has_audio"]),
                         "youtube": {"video_id": v["id"], "model": model if ai_ok else None,
                                     "options": {k: prm.get(k) for k in ("target_min", "cold_open", "captions", "style_id")},
                                     "style": learn_name(style_id), "director": dstats,
                                     "titles": meta.get("titles", []), "description": description,
                                     "tags": meta.get("tags", []), "thumbnail_text": meta.get("thumbnail_text", []),
                                     "thumbnails": thumbs, "chapters": [[t, c] for t, c in chapters]}},
                        ensure_ascii=False),
             json.dumps(tr, ensure_ascii=False), json.dumps(tl, ensure_ascii=False), json.dumps(st)))
        pid = cur.lastrowid
    projects.save(pid, note="Resumen para YouTube")
    return {"project_id": pid, "duration": round(tl["duration"], 1), "segments": len(segs),
            "chapters": len(chapters), "ai": ai_ok, "model": model if ai_ok else None, "director": dstats}
