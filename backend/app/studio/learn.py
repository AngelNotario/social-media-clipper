"""Aprender el estilo de edición de un video de referencia ("desarmarlo").

Método (inspirado en las skills video-understanding y watch, pero 100 % local):
- Medición con código: cortes por 10 s, destellos/fundidos, palabras por minuto, pausas, música de fondo,
  efectos de sonido, paleta de colores.
- Mirada con IA de visión (qwen3.6) en el gancho (primeros 3 s) y en el resto: subtítulos (posición,
  palabras por pantalla, mayúsculas, colores, tipo de letra), títulos, composición, zooms, emojis.
- Interpretación con qwen3: por qué funciona y qué copiar.
El resultado es un ESTILO aplicable a tus proyectos del Studio y a los clips.
"""
from __future__ import annotations

import base64
import json
import statistics
import subprocess
from collections import Counter
from pathlib import Path

import numpy as np

from .. import config, db
from ..ai import audio, llm, transcribe
from . import style as style_mod
from .probe import probe_any

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS learned_styles (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT NOT NULL,
    source     TEXT,
    profile    TEXT NOT NULL,
    style      TEXT NOT NULL,
    report     TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
"""

FONT_MAP = {"bold_sans": "Arial Black", "condensed": "Impact", "rounded": "Segoe UI Black",
            "regular_sans": "Segoe UI Semibold", "serif": "Georgia", "handwritten": "Comic Sans MS",
            "mono_pixel": "Consolas"}

FRAME_SCHEMA = {
    "type": "object",
    "properties": {
        "captions": {"type": "boolean"},
        "caption_position": {"type": "string", "enum": ["top", "middle", "bottom", "none"]},
        "words_on_screen": {"type": "integer"},
        "uppercase": {"type": "boolean"},
        "text_color": {"type": "string"},
        "highlight_color": {"type": "string"},
        "font_style": {"type": "string", "enum": list(FONT_MAP)},
        "title_box": {"type": "boolean"},
        "title_bg": {"type": "string"},
        "layout": {"type": "string", "enum": ["talking_head", "gameplay", "broll", "split_screen", "screen_recording", "text_only", "other"]},
        "zoomed_in": {"type": "boolean"},
        "emojis": {"type": "boolean"},
        "graphics": {"type": "string"},
    },
    "required": ["captions", "caption_position", "words_on_screen", "uppercase", "text_color", "highlight_color",
                 "font_style", "title_box", "title_bg", "layout", "zoomed_in", "emojis", "graphics"],
}
FRAME_PROMPT = ("Analiza este fotograma de un video corto como editor profesional. Indica: si hay subtítulos y "
                "su posición, cuántas palabras se ven a la vez, si van en mayúsculas, color del texto y color de la "
                "palabra resaltada (hex #RRGGBB, o \"\" si no hay), estilo de letra, si hay un título con caja de "
                "fondo y su color, la composición (layout), si la toma está muy cerrada/zoom, si hay emojis, y "
                "qué gráficos hay (stickers, flechas, barras de progreso...) en pocas palabras.")

ELEMENT_SCHEMA = {
    "type": "object",
    "properties": {
        "overlay": {"type": "string", "enum": ["none", "card", "fullscreen_broll", "pip", "screenshot", "meme"]},
        "overlay_count": {"type": "integer"},
        "speaker_visible": {"type": "boolean"},
        "speaker_layout": {"type": "string", "enum": ["full", "corner", "side", "none"]},
        "background": {"type": "string", "enum": ["room", "chroma_replaced", "whiteboard_graphics", "gameplay", "other"]},
        "text_on_screen": {"type": "boolean"},
        "text_kind": {"type": "string", "enum": ["none", "caption", "title", "label", "meme_text"]},
        "animation_hint": {"type": "string", "enum": ["none", "zoom", "pop", "slide"]},
    },
    "required": ["overlay", "overlay_count", "speaker_visible", "speaker_layout", "background", "text_on_screen",
                 "text_kind", "animation_hint"],
}
ELEMENT_PROMPT = ("Fotograma de un video de YouTube. Como editor, di SOLO qué elementos de edición se ven (no el tema): "
                  "overlay = imagen/recuadro encima del video (card: imagen o logo en un recuadro; screenshot: captura "
                  "de web/redes; fullscreen_broll: toma o clip que ocupa toda la pantalla; pip: video pequeño encima; "
                  "meme; none), cuántos overlays, si se ve la persona que habla y cómo (full, corner, side, none), "
                  "el fondo (room, chroma_replaced, whiteboard_graphics, gameplay, other), si hay texto en pantalla y "
                  "de qué tipo (caption = subtítulos, title, label = rótulo/dato, meme_text, none) y si parece haber "
                  "zoom, aparición (pop) o deslizamiento (slide).")
ELEMENT_STEP_S = 8.0
ELEMENT_MAX_FRAMES = 40
ELEMENT_WINDOW_S = 600.0

REPORT_SYSTEM = """Eres un editor senior que "desarma" videos virales para aprender su estilo.
Recibes mediciones hechas con código (si hay varios videos, la mediana de todos y un resumen por video),
observaciones de fotogramas y un análisis de sonido (efectos por minuto, tipos de efecto, música, sincronía
de los sonidos con los cortes). Escribe en español, en Markdown breve:
## El formato en una línea
## Gancho (primeros 3 s)
## Ritmo de edición
## Subtítulos y diseño
## Sonido (efectos, música y cómo se sincronizan con los cortes)
## Qué tienen en común (si hay varios videos)
## Qué copiar (por orden de impacto)
## Qué NO copiar
Cita los números medidos. No inventes nada que no esté en los datos."""


def ensure_schema() -> None:
    with db.get_conn() as conn:
        conn.executescript(SCHEMA_SQL)


def _luma_series(path: Path, start: float, dur: float, fps: int = 10) -> np.ndarray:
    r = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{start:.2f}", "-i", str(path), "-t", f"{dur:.2f}",
                        "-vf", f"fps={fps},scale=64:36,format=gray", "-f", "rawvideo", "-"],
                       capture_output=True, timeout=900)
    data = np.frombuffer(r.stdout, dtype=np.uint8)
    n = 64 * 36
    if data.size < n:
        return np.array([])
    return data[: (data.size // n) * n].reshape(-1, n).mean(axis=1)


def _flashes_fades(luma: np.ndarray, fps: int = 10) -> tuple[int, int]:
    flashes = fades = 0
    i = 1
    while i < len(luma) - 2:
        if luma[i] - luma[i - 1] > 55 and luma[min(i + 3, len(luma) - 1)] < luma[i] - 35:
            flashes += 1
            i += 3
            continue
        if luma[i] < 14 and luma[i - 1] > 40:
            fades += 1
            i += 3
            continue
        i += 1
    return flashes, fades


def _palette(frames: list[Path], k: int = 4) -> list[str]:
    try:
        import cv2
    except ImportError:
        return []
    px = []
    for f in frames:
        img = cv2.imread(str(f))
        if img is None:
            continue
        small = cv2.resize(img, (48, 85 if img.shape[0] > img.shape[1] else 27))
        px.append(small.reshape(-1, 3))
    if not px:
        return []
    data = np.concatenate(px).astype(np.float32)
    crit = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 20, 1.0)
    _, labels, centers = cv2.kmeans(data, k, None, crit, 3, cv2.KMEANS_PP_CENTERS)
    order = np.argsort(-np.bincount(labels.flatten(), minlength=k))
    return ["#%02X%02X%02X" % (int(c[2]), int(c[1]), int(c[0])) for c in centers[order]]


def _vote(values, default=None):
    values = [v for v in values if v not in (None, "", "none")]
    return Counter(values).most_common(1)[0][0] if values else default


def _hex(v: str) -> str | None:
    v = (v or "").strip().upper()
    if not v.startswith("#"):
        v = "#" + v
    return v if len(v) == 7 and all(c in "0123456789ABCDEF" for c in v[1:]) else None


def _av_sync(energy: list[float], hop: float, cut_times: list[float],
             scene_times: list[tuple[float, float]], dur: float) -> dict:
    """Sincronía audio-video: cuántos cortes y cambios de escena coinciden con picos de energía de audio,
    y cuántos momentos de silencio coincenden con planos estáticos. Devuelve 0-100 y los momentos clave."""
    if not energy or not cut_times:
        return {"score": 0.0, "sync_cuts": 0, "total_cuts": 0, "moments": []}
    # picos de audio (derivada positiva fuerte)
    peaks = [i * hop for i in range(1, len(energy))
             if energy[i] - energy[i - 1] > 0.30]
    # momentos de silencio (energía baja sostenida)
    silences = [i * hop for i in range(1, len(energy))
                if energy[i] < 0.08 and energy[i - 1] < 0.08]
    tol = 0.6  # segundos de tolerancia para considerar que coincide
    sync_cuts = 0
    matched_peaks = set()
    for ct in cut_times:
        for p in peaks:
            if abs(p - ct) <= tol and p not in matched_peaks:
                sync_cuts += 1
                matched_peaks.add(p)
                break
    score = round(100 * sync_cuts / max(len(cut_times), 1), 1)
    # momentos clave AV: corte + pico de audio en el mismo segundo
    moments = []
    for ct in cut_times:
        near = [p for p in peaks if abs(p - ct) <= tol]
        if near:
            moments.append({"t": round(ct, 3), "type": "cut_on_beat",
                            "audio_peaks": round(len(near), 1)})
    return {"score": score, "sync_cuts": sync_cuts, "total_cuts": len(cut_times),
            "silence_moments": len(silences), "moments": moments[:20]}


def _caption_events(words: list[dict], max_gap: float = 0.7) -> list[dict]:
    """Agrupa palabras consecutivas en frases de subtítulo con rango de tiempo y texto real."""
    events, cur = [], None
    for w in words:
        if cur is None:
            cur = [w]
        elif w["start"] - cur[-1]["end"] <= max_gap:
            cur.append(w)
        else:
            events.append({"t": round(cur[0]["start"], 3), "end": round(cur[-1]["end"], 3),
                           "text": " ".join(x["word"] for x in cur), "type": "caption"})
            cur = [w]
    if cur:
        events.append({"t": round(cur[0]["start"], 3), "end": round(cur[-1]["end"], 3),
                       "text": " ".join(x["word"] for x in cur), "type": "caption"})
    return events


def analyze(path: Path, progress) -> dict:
    meta = probe_any(path)
    dur = meta["duration"] or 0
    win = min(dur, 180.0)  # los videos cortos se analizan completos; los largos, sus primeros 3 min
    work = config.WORK_DIR / f"learn_{path.stem[:20]}"
    work.mkdir(parents=True, exist_ok=True)
    out: dict = {"duration": round(dur, 1), "analyzed_seconds": round(win, 1),
                 "vertical": (meta["height"] or 0) > (meta["width"] or 1)}

    progress(0.05, "Midiendo cortes y transiciones")
    ewin = min(dur, ELEMENT_WINDOW_S)  # ventana larga para el vocabulario visual (tarjetas, b-roll, textos)
    all_scenes = audio.detect_scenes(path, threshold=0.3)
    scenes = [t for t in all_scenes if t <= win]
    scenes_e = [t for t in all_scenes if t <= ewin]
    luma = _luma_series(path, 0, win)
    flashes, fades = _flashes_fades(luma)
    shot_lens = np.diff([0.0] + scenes + [win]) if win else np.array([0])
    out["edit"] = {"cuts": len(scenes), "cuts_per_10s": round(len(scenes) / max(win, 1) * 10, 2),
                   "median_shot_s": round(float(np.median(shot_lens)), 2) if len(shot_lens) else None,
                   "first_cut_s": round(scenes[0], 2) if scenes else None,
                   "flashes": flashes, "dips_to_black": fades,
                   "cuts_per_min": round(len(scenes_e) / max(ewin, 1) * 60, 2), "window_s": round(ewin, 1)}
    out["cut_events"] = [{"t": round(t, 3), "type": "cut"} for t in scenes]
    out["scene_events"] = [{"t": round(a, 3), "end": round(b, 3), "type": "scene"}
                           for a, b in zip([0.0] + scenes, scenes + [win]) if b - a >= 0.1]

    if meta["has_audio"]:
        progress(0.2, "Escuchando: voz, pausas, música y efectos")
        wav = work / "a.wav"
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(path), "-t", f"{win:.2f}", "-vn", "-ac", "1",
                        "-ar", "16000", "-c:a", "pcm_s16le", str(wav)], capture_output=True)
        energy = audio.energy_curve(wav)
        tr = transcribe.transcribe(wav, db.get_setting("whisper_model"), None, win)
        words = [w for s in tr["segments"] for w in s.get("words", [])]
        out["caption_events"] = _caption_events(words)
        gaps = [b["start"] - a["end"] for a, b in zip(words, words[1:])]
        hop = audio.ENERGY_HOP
        speech = set()
        for w in words:
            speech.update(range(int(w["start"] / hop), int(w["end"] / hop) + 1))
        gap_e = [e for i, e in enumerate(energy) if i not in speech]
        sp_e = [e for i, e in enumerate(energy) if i in speech]
        bed = None
        if len(gap_e) >= 4 and sp_e:
            ratio = statistics.median(gap_e) / max(statistics.median(sp_e), 1e-3)
            bed = "sí" if ratio > 0.55 else ("tenue" if ratio > 0.35 else "no")
        jump_times = [round(i * hop, 3) for i in range(1, len(energy))
                      if energy[i] - energy[i - 1] > 0.35 and i not in speech]
        jumps = len(jump_times)
        out["sound"] = {"music_bed": bed, "sfx_estimate": jumps}
        out["sound_events"] = [{"t": t, "type": "sfx"} for t in jump_times]
        out["delivery"] = {"language": tr["language"], "words": len(words),
                           "words_per_min": round(len(words) / max(win, 1) * 60),
                           "median_pause_s": round(statistics.median(gaps), 2) if gaps else None,
                           "long_pauses": sum(1 for g in gaps if g > 0.6),
                           "hook_text": " ".join(w["word"] for w in words if w["start"] < 3.2)[:200]}
        out["sound"] = {"music_bed": bed, "sfx_estimate": jumps}
        wav.unlink(missing_ok=True)

        # Sincronía audio-video: cortes que caen en picos de energía de audio
        cut_times = [float(e["t"]) for e in out.get("cut_events", [])]
        scene_spans = [(float(e["t"]), float(e.get("end", e["t"]))) for e in out.get("scene_events", [])]
        out["audio_visual"] = _av_sync(energy, hop, cut_times, scene_spans, win)
        out["sound_events_av"] = [
            {"t": float(e["t"]), "type": "sfx_on_beat" if any(abs(float(e["t"]) - p) <= 0.6
                                                              for p in [i * hop for i in range(1, len(energy))
                                                                        if energy[i] - energy[i - 1] > 0.30])
             else "sfx"} for e in out.get("sound_events", [])
        ]

    progress(0.4, "Mirando fotogramas con IA de visión")
    times = [0.4, 1.4, 2.6] + [round(3 + (win - 3) * (i + 0.5) / 7, 2) for i in range(7)] if win > 4 else [win / 2]
    frames, obs = [], []
    url, vmodel = db.get_setting("ollama_url"), db.get_setting("vision_model") or "qwen3.6:35b-a3b"
    st = llm.status(url)
    vision_ok = st["available"] and vmodel in st["models"]
    for i, t in enumerate(times):
        f = work / f"f{i:02d}.jpg"
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", f"{t:.2f}", "-i", str(path), "-frames:v", "1",
                        "-vf", "scale=540:-2", "-q:v", "4", str(f)], capture_output=True)
        if not f.exists():
            continue
        frames.append(f)
        if vision_ok:
            progress(0.4 + 0.35 * i / len(times), f"Analizando fotograma {i + 1}/{len(times)}")
            try:
                d = llm.chat_json(url, vmodel, "Eres un editor de video. Responde solo JSON.", FRAME_PROMPT,
                                  FRAME_SCHEMA, images=[base64.b64encode(f.read_bytes()).decode()],
                                  num_ctx=4096, keep_alive="2m", timeout=300, max_tokens=300)
                d["t"] = t
                obs.append(d)
            except llm.LLMError:
                pass
    if vision_ok:
        el = elements(path, ewin, url, vmodel, work, lambda x, m: progress(0.76 + 0.2 * x, m))
        if el:
            out["elements"] = el.get("summary") if isinstance(el, dict) and "summary" in el else el
            out["element_events"] = el.get("events") if isinstance(el, dict) and "events" in el else []
        llm.unload(url, vmodel)
    out["palette"] = _palette(frames)
    out["design_events"] = obs
    if obs:
        cap = [o for o in obs if o.get("captions")]
        out["design"] = {
            "captions_ratio": round(len(cap) / len(obs), 2),
            "caption_position": _vote([o.get("caption_position") for o in cap], "bottom"),
            "words_on_screen": int(statistics.median([max(1, int(o.get("words_on_screen") or 1)) for o in cap])) if cap else None,
            "uppercase": _vote([o.get("uppercase") for o in cap], True),
            "text_color": _vote([_hex(o.get("text_color")) for o in cap]),
            "highlight_color": _vote([_hex(o.get("highlight_color")) for o in cap]),
            "font_style": _vote([o.get("font_style") for o in cap], "bold_sans"),
            "title_box": _vote([o.get("title_box") for o in obs], False),
            "title_bg": _vote([_hex(o.get("title_bg")) for o in obs if o.get("title_box")]),
            "layout": _vote([o.get("layout") for o in obs], "other"),
            "zoomed_ratio": round(sum(1 for o in obs if o.get("zoomed_in")) / len(obs), 2),
            "emojis": _vote([o.get("emojis") for o in obs], False),
            "graphics": [o.get("graphics") for o in obs if o.get("graphics")][:6],
            "hook_frames": [o for o in obs if o.get("t", 99) < 3],
        }
    for f in frames:
        f.unlink(missing_ok=True)
    try:
        work.rmdir()
    except OSError:
        pass
    return out


def element_times(window_s: float) -> list[float]:
    """Momentos a mirar: uno cada ~8 s (máximo 40), repartidos en la ventana."""
    if window_s <= 1:
        return [max(window_s / 2, 0.0)]
    step = max(ELEMENT_STEP_S, window_s / ELEMENT_MAX_FRAMES)
    n = max(1, int(window_s // step))
    return [round(step * (i + 0.5), 2) for i in range(n) if step * (i + 0.5) < window_s]


def summarize_elements(obs: list[dict], window_s: float) -> dict | None:
    """Fotogramas observados (en orden) → vocabulario visual: cuánto y qué tipo de recursos pone el editor."""
    obs = [o for o in obs if isinstance(o, dict)]
    if not obs:
        return None
    n = len(obs)
    mins = max(window_s, 1.0) / 60
    has_ov = [o.get("overlay") not in (None, "none") for o in obs]
    ov = [o for o, h in zip(obs, has_ov) if h]

    def appearances(flags: list[bool], keys: list) -> int:  # cuenta cuando algo aparece o cambia
        c = 0
        for i, f in enumerate(flags):
            if f and (i == 0 or not flags[i - 1] or keys[i] != keys[i - 1]):
                c += 1
        return c
    ov_keys = [(o.get("overlay"), o.get("overlay_count")) for o in obs]
    lab = [o.get("text_kind") in ("label", "title") for o in obs]
    zoom = [o.get("animation_hint") == "zoom" for o in obs]
    nov = max(len(ov), 1)
    return {
        "overlay_ratio": round(len(ov) / n, 2),
        "overlays_per_min": round(appearances(has_ov, ov_keys) / mins, 2),
        "card_ratio": round(sum(1 for o in ov if o.get("overlay") in ("card", "screenshot")) / nov, 2) if ov else 0.0,
        "fullscreen_ratio": round(sum(1 for o in ov if o.get("overlay") == "fullscreen_broll") / nov, 2) if ov else 0.0,
        "pip_ratio": round(sum(1 for o in ov if o.get("overlay") == "pip") / nov, 2) if ov else 0.0,
        "speaker_visible_ratio": round(sum(1 for o in obs if o.get("speaker_visible")) / n, 2),
        "speaker_layout": _vote([o.get("speaker_layout") for o in obs], "none"),
        "background": _vote([o.get("background") for o in obs], "other"),
        "text_ratio": round(sum(1 for o in obs if o.get("text_on_screen")) / n, 2),
        "labels_ratio": round(sum(lab) / n, 2),
        "labels_per_min": round(appearances(lab, [o.get("text_kind") for o in obs]) / mins, 2),
        "memes_ratio": round(sum(1 for o in obs if o.get("overlay") == "meme" or o.get("text_kind") == "meme_text") / n, 2),
        "zoom_ratio": round(sum(zoom) / n, 2),
        "zoom_per_min": round(appearances(zoom, [True] * n) / mins, 2),
        "sampled": n, "window_s": round(window_s, 1),
    }


def elements(path: Path, window_s: float, url: str, vmodel: str, work: Path, progress=lambda p, m: None) -> dict | None:
    """Mira un fotograma cada ~8 s de los primeros 10 min con la IA de visión y resume los elementos de edición."""
    times = element_times(window_s)
    obs = []
    for i, t in enumerate(times):
        f = work / f"e{i:02d}.jpg"
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", f"{t:.2f}", "-i", str(path), "-frames:v", "1",
                        "-vf", "scale=512:-2", "-q:v", "5", str(f)], capture_output=True)
        if not f.exists():
            continue
        progress(i / max(len(times), 1), f"Mirando recursos y gráficos {i + 1}/{len(times)}")
        try:
            d = llm.chat_json(url, vmodel, "Eres un editor de video. Responde solo JSON.", ELEMENT_PROMPT,
                              ELEMENT_SCHEMA, images=[base64.b64encode(f.read_bytes()).decode()],
                              num_ctx=4096, keep_alive="2m", timeout=240, max_tokens=200)
            d["t"] = t
            obs.append(d)
        except llm.LLMError:
            pass
        finally:
            f.unlink(missing_ok=True)
    return {"summary": summarize_elements(obs, window_s), "events": obs}


def elements_style(prof: dict) -> dict:
    """Objetivos de edición sacados del vocabulario visual (no caben en el estilo de render; los usa el juez)."""
    el = prof.get("elements") or {}
    if not el:
        return {}
    ratios = {"card": el.get("card_ratio") or 0, "full": el.get("fullscreen_ratio") or 0, "pip": el.get("pip_ratio") or 0}
    return {"broll_per_min": el.get("overlays_per_min"),
            "card_style": max(ratios, key=ratios.get) if any(ratios.values()) else "card",
            "labels": (el.get("labels_ratio") or 0) >= 0.08,
            "memes": (el.get("memes_ratio") or 0) >= 0.05}


def build_edl(prof: dict) -> list[dict]:
    """Desarme frame-accurate del video de referencia: todos los eventos de edición con timestamp exacto.
    Tipos: cut, scene, caption, sfx, overlay/<mode>, text, zoom, transition, music, layout, design.
    Sirve para que el agente sepa EXACTAMENTE qué poner en cada segundo y no improvisar."""
    dur = float(prof.get("duration") or 0) or 1.0
    events: list[dict] = []

    def add(t, etype, **kw):
        ev = {"t": round(float(t), 3), "type": etype}
        ev.update(kw)
        events.append(ev)

    # Cortes y planos (del desarme de escenas)
    for ev in prof.get("cut_events") or []:
        add(ev["t"], "cut")
    for ev in prof.get("scene_events") or []:
        add(ev["t"], "scene", end=ev.get("end"))

    # Texto real hablado (subtítulos/captions con su frase)
    for ev in prof.get("caption_events") or []:
        add(ev["t"], "caption", end=ev.get("end"), text=ev.get("text"))

    # Sonidos detectados por energía (candidatos a SFX)
    for ev in prof.get("sound_events") or []:
        add(ev["t"], "sfx")
    # Sonidos con sincronía AV (cortes que caen en picos de audio)
    for ev in prof.get("sound_events_av") or []:
        etype = "sfx_on_beat" if ev.get("type") == "sfx_on_beat" else "sfx"
        add(ev["t"], etype, on_beat=bool(ev.get("on_beat")))

    # Elementos visuales de la IA de visión (tarjetas, fullscreen, pip, meme, fondo, texto en pantalla)
    for ev in prof.get("element_events") or []:
        t = ev.get("t")
        ov = ev.get("overlay") or "none"
        if ov and ov != "none":
            add(t, f"overlay/{ov}", overlay_count=ev.get("overlay_count", 1),
                speaker_visible=ev.get("speaker_visible"), speaker_layout=ev.get("speaker_layout"),
                background=ev.get("background"), animation=ev.get("animation_hint"))
        if ev.get("text_on_screen"):
            add(t, "text", text_kind=ev.get("text_kind"))

    # Diseño de fotograma (subtítulos, layout, zoom, emojis, gráficos)
    for ev in prof.get("design_events") or []:
        t = ev.get("t")
        if ev.get("layout"):
            add(t, "layout", layout=ev.get("layout"))
        if ev.get("zoomed_in"):
            add(t, "zoom")
        if ev.get("captions"):
            add(t, "caption_design", position=ev.get("caption_position"),
                words=ev.get("words_on_screen"), uppercase=ev.get("uppercase"),
                font=ev.get("font_style"), color=ev.get("text_color"),
                highlight=ev.get("highlight_color"))

    # Música (estimada): si hay música de fondo, la marca como presente desde el principio
    bed = (prof.get("sound") or {}).get("music_bed")
    if bed in ("sí", "tenue"):
        add(0.0, "music", kind=bed)

    events.sort(key=lambda ev: (ev["t"], ev["type"]))
    return events


def to_style(prof: dict, base: dict | None = None) -> dict:
    s = dict(style_mod.clean_style(base))
    e, d, snd, dl = prof.get("edit", {}), prof.get("design") or {}, prof.get("sound") or {}, prof.get("delivery") or {}
    cps = e.get("cuts_per_10s") or 0
    if cps >= 3:
        s.update(zoom_every=1, zoom_amount=1.15, sfx_density=0.5)
    elif cps >= 1.5:
        s.update(zoom_every=1, zoom_amount=1.12, sfx_density=0.35)
    elif cps >= 0.6:
        s.update(zoom_every=2, zoom_amount=1.1, sfx_density=0.2)
    else:
        s.update(zoom_every=3, zoom_amount=1.06, sfx_density=0.1)
    if (d.get("zoomed_ratio") or 0) > 0.5:
        s["zoom_amount"] = max(s["zoom_amount"], 1.18)
    if (e.get("flashes") or 0) >= 2:
        s["transition_style"] = "dinamico"
    elif (e.get("dips_to_black") or 0) >= 2:
        s["transition_style"] = "suave"
    elif cps < 0.6:
        s["transition_style"] = "cortes"
    if dl.get("median_pause_s") is not None:
        s["silence_threshold"] = round(max(0.2, min(1.0, dl["median_pause_s"] * 2.2 + 0.15)), 2)
    if d:
        if (d.get("captions_ratio") or 0) < 0.25:
            s["caption_style"] = "none"
        else:
            s["caption_style"] = "karaoke" if d.get("highlight_color") else "simple"
            # "abajo" del video de referencia = automático: abajo en recortado, bajo la cámara en modo stream
            s["caption_position"] = d.get("caption_position") if d.get("caption_position") in ("top", "middle") else "auto"
            if d.get("words_on_screen"):
                s["caption_max_words"] = max(1, min(6, d["words_on_screen"]))
            s["caption_uppercase"] = bool(d.get("uppercase"))
            s["caption_font"] = FONT_MAP.get(d.get("font_style"), s["caption_font"])
            s["title_font"] = s["caption_font"]
            if d.get("text_color"):
                s["caption_color"] = d["text_color"]
            if d.get("highlight_color"):
                s["highlight_color"] = d["highlight_color"]
                s["callout_color"] = d["highlight_color"]
        if d.get("title_bg"):
            s["title_bg"] = d["title_bg"]
    if snd.get("music_bed") == "no":
        s["music_volume"] = 0.0
    elif snd.get("music_bed") == "tenue":
        s["music_volume"] = 0.08
    elif snd.get("music_bed") == "sí":
        s["music_volume"] = 0.16
    sp = prof.get("sound_pro") or {}
    if sp and not sp.get("error"):  # lo que earshot escuchó manda sobre la estimación por energía
        spm = float(sp.get("sfx_per_min") or 0)
        s["sfx_per_min"] = round(spm, 1)
        s["sfx_density"] = round(max(0.05, min(0.7, spm / 30)), 2)
        roles = [r for r in (sp.get("roles") or []) if r in ("impacto", "revelacion", "fallo", "suspenso", "exito",
                                                              "transicion", "texto")]
        s["sound_roles"] = ",".join(roles[:6])
        s["sfx_on_cuts"] = bool((sp.get("cut_sync") or 0) >= 0.6 and "transicion" in roles)
        mr = sp.get("music_ratio")
        if isinstance(mr, (int, float)) and snd.get("music_bed") in (None, "no", "tenue"):
            s["music_volume"] = 0.0 if mr < 0.15 else (0.1 if mr < 0.5 else 0.15)
    av = prof.get("audio_visual") or {}
    if av and av.get("score") is not None:
        av_score = float(av["score"])
        if av_score >= 70:
            s["sfx_on_cuts"] = True
            s["sfx_density"] = round(max(s.get("sfx_density", 0.2), 0.4), 2)
        elif av_score >= 40:
            s["sfx_on_cuts"] = bool(s.get("sfx_on_cuts", False))
            s["sfx_density"] = round(max(s.get("sfx_density", 0.2), 0.3), 2)
        else:
            s["sfx_on_cuts"] = False
            s["sfx_density"] = round(min(s.get("sfx_density", 0.2), 0.2), 2)
        s["av_sync_score"] = av_score
    pal = prof.get("palette") or []
    if len(pal) >= 2:
        s["bg_color_1"], s["bg_color_2"] = pal[0], pal[1]
    s["callouts_per_min"] = 6 if cps >= 2 else 4
    es = elements_style(prof)
    if es:
        s["memes"] = bool(es["memes"])
        lpm = (prof.get("elements") or {}).get("labels_per_min")
        if isinstance(lpm, (int, float)):
            s["callouts_per_min"] = max(0, min(12, round(lpm)))
    return style_mod.clean_style(s)


def write_report(prof: dict) -> str:
    url, model = db.get_setting("ollama_url"), db.get_setting("ollama_model")
    st = llm.status(url)
    if not (st["available"] and model in st["models"]):
        return "_(Sin IA disponible: solo mediciones)_\n\n```json\n" + json.dumps(prof, ensure_ascii=False, indent=1) + "\n```"
    data = {k: v for k, v in prof.items() if k not in ("design", "videos", "failed")}
    if prof.get("videos") and len(prof["videos"]) > 1:  # resumen por video para que note lo que se repite
        data["por_video"] = [{"titulo": v["source"].get("title"), "cortes_10s": (v["profile"].get("edit") or {}).get("cuts_per_10s"),
                              "palabras_min": (v["profile"].get("delivery") or {}).get("words_per_min"),
                              "sfx_min": (v["profile"].get("sound_pro") or {}).get("sfx_per_min")} for v in prof["videos"][:12]]
    if prof.get("design"):
        data["design"] = {k: v for k, v in prof["design"].items() if k != "hook_frames"}
        data["hook_frames"] = prof["design"].get("hook_frames")
    try:
        r = llm.chat_json(url, model, REPORT_SYSTEM, "DATOS:\n" + json.dumps(data, ensure_ascii=False),
                          {"type": "object", "properties": {"markdown": {"type": "string"}}, "required": ["markdown"]},
                          keep_alive="0s")
        return r.get("markdown", "")
    except llm.LLMError:
        return ""


EARSHOT_WORKER = Path(__file__).resolve().parents[2] / "earshot_worker.py"


def earshot_python() -> Path | None:
    base = Path(__file__).resolve().parents[2] / ".venv-earshot"
    for p in (base / "Scripts" / "python.exe", base / "bin" / "python"):
        if p.exists():
            return p
    return None


def listen(paths: list[Path], max_s: float = 180.0, progress=lambda p, s: None) -> dict:
    """Earshot escucha los videos (una sola carga de modelos para todos). {ruta: resumen} o {} si no está."""
    py = earshot_python()
    if not py or not paths or db.get_setting("earshot_enabled") == "0":
        return {}
    out = config.WORK_DIR / f"earshot_{abs(hash(tuple(map(str, paths)))) % 10**8}.json"
    progress(0.0, f"Escuchando el diseño de sonido de {len(paths)} video(s) con earshot")
    try:
        r = subprocess.run([str(py), str(EARSHOT_WORKER), str(out), *map(str, paths), "--max", str(max_s)],
                           capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=300 + 120 * len(paths))
        if not out.exists():
            return {str(p): {"error": (r.stdout or r.stderr).strip()[-300:]} for p in paths}
        data = json.loads(out.read_text(encoding="utf-8"))
        out.unlink(missing_ok=True)
        return data
    except (subprocess.TimeoutExpired, OSError, json.JSONDecodeError) as exc:
        return {str(p): {"error": str(exc)[:200]} for p in paths}


def _num(vals):
    vals = [float(v) for v in vals if isinstance(v, (int, float))]
    return round(statistics.median(vals), 2) if vals else None


def combine(profiles: list[dict]) -> dict:
    """Perfil común de varios videos: medianas para las medidas y votos para lo visual.
    Así el estilo aprendido es lo que se repite entre tus referencias, no lo de un video suelto."""
    if len(profiles) == 1:
        return {**profiles[0], "n_videos": 1}
    out: dict = {"n_videos": len(profiles), "duration": _num([p.get("duration") for p in profiles]),
                 "analyzed_seconds": _num([p.get("analyzed_seconds") for p in profiles]),
                 "vertical": _vote([p.get("vertical") for p in profiles], False)}
    edits = [p.get("edit") or {} for p in profiles]
    out["edit"] = {k: _num([e.get(k) for e in edits]) for k in ("cuts", "cuts_per_10s", "median_shot_s", "first_cut_s",
                                                               "flashes", "dips_to_black", "cuts_per_min", "window_s")}
    els = [p["elements"] for p in profiles if p.get("elements")]
    if els:
        nums = ("overlay_ratio", "overlays_per_min", "card_ratio", "fullscreen_ratio", "pip_ratio", "speaker_visible_ratio",
                "text_ratio", "labels_ratio", "labels_per_min", "memes_ratio", "zoom_ratio", "zoom_per_min", "window_s")
        out["elements"] = {k: _num([e.get(k) for e in els]) for k in nums}
        out["elements"]["speaker_layout"] = _vote([e.get("speaker_layout") for e in els], "none")
        out["elements"]["background"] = _vote([e.get("background") for e in els], "other")
        out["elements"]["sampled"] = sum(int(e.get("sampled") or 0) for e in els)
    dels = [p["delivery"] for p in profiles if p.get("delivery")]
    if dels:
        out["delivery"] = {k: _num([d.get(k) for d in dels]) for k in ("words", "words_per_min", "median_pause_s", "long_pauses")}
        out["delivery"]["language"] = _vote([d.get("language") for d in dels])
        out["delivery"]["hook_text"] = " | ".join((d.get("hook_text") or "")[:80] for d in dels[:4])
    snds = [p["sound"] for p in profiles if p.get("sound")]
    if snds:
        out["sound"] = {"music_bed": _vote([x.get("music_bed") for x in snds]), "sfx_estimate": _num([x.get("sfx_estimate") for x in snds])}
    avs = [p["audio_visual"] for p in profiles if p.get("audio_visual")]
    if avs:
        out["audio_visual"] = {
            "score": round(_num([a.get("score") for a in avs if a.get("score") is not None]) or 0.0, 1),
            "sync_cuts": sum(int(a.get("sync_cuts") or 0) for a in avs),
            "total_cuts": sum(int(a.get("total_cuts") or 0) for a in avs),
            "silence_moments": sum(int(a.get("silence_moments") or 0) for a in avs),
            "moments": [m for a in avs for m in (a.get("moments") or [])][:30],
        }
    pros = [p["sound_pro"] for p in profiles if p.get("sound_pro") and not p["sound_pro"].get("error")]
    if pros:
        tags = Counter()
        for x in pros:
            tags.update(dict(x.get("top_sfx") or []))
        out["sound_pro"] = {
            "sfx_per_min": _num([x.get("sfx_per_min") for x in pros]),
            "music_ratio": _num([x.get("music_ratio") for x in pros]),
            "tempo": _num([x.get("tempo") for x in pros]),
            "lufs": _num([x.get("lufs") for x in pros]),
            "cut_sync": _num([x.get("cut_sync") for x in pros]),
            "top_sfx": tags.most_common(8),
            "roles": [r for r, _ in Counter(r for x in pros for r in (x.get("roles") or [])).most_common(6)],
        }
    designs = [p["design"] for p in profiles if p.get("design")]
    if designs:
        out["design"] = {
            "captions_ratio": _num([d.get("captions_ratio") for d in designs]),
            "caption_position": _vote([d.get("caption_position") for d in designs], "bottom"),
            "words_on_screen": int(_num([d.get("words_on_screen") for d in designs]) or 0) or None,
            "uppercase": _vote([d.get("uppercase") for d in designs], True),
            "text_color": _vote([d.get("text_color") for d in designs]),
            "highlight_color": _vote([d.get("highlight_color") for d in designs]),
            "font_style": _vote([d.get("font_style") for d in designs], "bold_sans"),
            "title_box": _vote([d.get("title_box") for d in designs], False),
            "title_bg": _vote([d.get("title_bg") for d in designs]),
            "layout": _vote([d.get("layout") for d in designs], "other"),
            "zoomed_ratio": _num([d.get("zoomed_ratio") for d in designs]),
            "emojis": _vote([d.get("emojis") for d in designs], False),
            "graphics": [g for d in designs for g in (d.get("graphics") or [])][:8],
            "hook_frames": [h for d in designs for h in (d.get("hook_frames") or [])][:6],
        }
    pal = Counter(c for p in profiles for c in (p.get("palette") or []))
    out["palette"] = [c for c, _ in pal.most_common(4)]
    return out


def _resolve(src: dict, progress) -> tuple[Path, dict]:
    """Un origen (enlace, video de la biblioteca o archivo subido) → archivo local + datos de la fuente."""
    if src.get("url"):
        from . import refs
        try:
            d = refs.download(src["url"], progress)
        except refs.RefError as exc:
            raise RuntimeError(str(exc)) from exc
        return Path(d["path"]), {"kind": "url", "url": d["url"], "title": d["title"], "uploader": d["uploader"],
                                 "platform": d["platform"]}
    if src.get("video_id"):
        with db.get_conn() as conn:
            v = conn.execute("SELECT stored_path, title FROM videos WHERE id = ?", (src["video_id"],)).fetchone()
        if not v:
            raise RuntimeError("Video no encontrado.")
        return Path(v["stored_path"]), {"kind": "library", "video_id": src["video_id"], "title": v["title"]}
    up = config.STUDIO_MEDIA_DIR / str(src.get("upload_id"))
    return up, {"kind": "upload", "title": src.get("source_name") or "Video subido"}


def _sources(prm: dict) -> list[dict]:
    from . import refs
    srcs = list(prm.get("sources") or [])
    srcs += [{"url": u} for u in refs.parse_urls(prm.get("urls"))]
    if prm.get("video_id"):
        srcs.append({"video_id": prm["video_id"]})
    for v in prm.get("video_ids") or []:
        srcs.append({"video_id": v})
    if prm.get("upload_id"):
        srcs.append({"upload_id": prm["upload_id"], "source_name": prm.get("source_name")})
    seen, out = set(), []
    for s in srcs:
        k = json.dumps(s, sort_keys=True)
        if k not in seen:
            seen.add(k)
            out.append(s)
    return out[:12]


def run_learn(job: dict, progress) -> dict:
    """Aprende un estilo de uno o varios videos (enlaces con yt-dlp, biblioteca o archivos subidos).
    Con style_id, los videos nuevos se suman a un estilo existente y el estilo se recalcula con todos."""
    ensure_schema()
    prm = job["params"]
    srcs = _sources(prm)
    if not srcs:
        raise RuntimeError("Agrega al menos un video o enlace.")
    prev: list[dict] = []
    if prm.get("style_id"):
        with db.get_conn() as conn:
            r = conn.execute("SELECT profile, name FROM learned_styles WHERE id = ?", (prm["style_id"],)).fetchone()
        if not r:
            raise RuntimeError("El estilo a ampliar ya no existe.")
        old = json.loads(r["profile"])
        prev = old.get("videos") or [{"source": {"title": "video original"}, "profile": old}]
    videos, failed = [], []
    n = len(srcs)
    for i, src in enumerate(srcs):
        base, span = i / n, 1 / n

        def sub(p, msg="", base=base, span=span, i=i):
            progress(min(0.9, base * 0.9 + span * 0.9 * p), f"Video {i + 1}/{n}: {msg}" if msg else None)
        try:
            path, info = _resolve(src, lambda p, m: sub(0.15 * p, m))
            if not path.exists():
                raise RuntimeError("El archivo ya no existe.")
            prof = analyze(path, lambda p, m=None: sub(0.15 + 0.85 * p, m or ""))
            videos.append({"source": info, "profile": prof, "_path": path})
        except Exception as exc:  # noqa: BLE001 — un enlace roto no tumba el resto
            failed.append({"source": src, "error": str(exc)[:300]})
    if videos:  # diseño de sonido con earshot: efectos y su tipo, música, sincronía con los cortes
        heard = listen([v["_path"] for v in videos], progress=lambda p, m: progress(0.86, m))
        for v in videos:
            sp = heard.get(str(v["_path"]))
            if sp:
                v["profile"]["sound_pro"] = sp
    for v in videos:
        v.pop("_path", None)
    if not videos and not prev:
        raise RuntimeError("No se pudo analizar ningún video: " + "; ".join(f["error"] for f in failed)[:600])
    allv = prev + videos
    agg = combine([v["profile"] for v in allv])
    agg["videos"] = allv
    agg["failed"] = failed
    progress(0.92, "Escribiendo el análisis del estilo")
    report = write_report(agg)
    style = to_style(agg)
    agg["elements_style"] = elements_style(agg)  # objetivos para el juez de estilo (tarjetas/min, tipo, rótulos)
    # Desarme frame-accurate del estilo: cada corte, escena, sonido, texto y recurso con su timestamp exacto.
    agg["edl"] = []
    for idx, v in enumerate(allv):
        for ev in build_edl(v["profile"]):
            ev2 = dict(ev)
            ev2["src"] = idx
            ev2["source"] = v["source"].get("title", f"video {idx + 1}")
            agg["edl"].append(ev2)
    agg["edl"].sort(key=lambda ev: (ev["t"], ev["type"]))
    titles = [v["source"].get("title", "") for v in allv]
    source = titles[0] if len(titles) == 1 else f"{len(titles)} videos: " + ", ".join(t[:30] for t in titles[:3])
    with db.get_conn() as conn:
        if prm.get("style_id"):
            conn.execute("UPDATE learned_styles SET source = ?, profile = ?, style = ?, report = ? WHERE id = ?",
                         (source[:300], json.dumps(agg, ensure_ascii=False), json.dumps(style), report, prm["style_id"]))
            sid = prm["style_id"]
        else:
            name = prm.get("name") or (f"Estilo de {titles[0]}" if len(titles) == 1 else f"Estilo de {len(titles)} videos")
            cur = conn.execute("INSERT INTO learned_styles (name, source, profile, style, report) VALUES (?, ?, ?, ?, ?)",
                               (name[:120], source[:300], json.dumps(agg, ensure_ascii=False), json.dumps(style), report))
            sid = cur.lastrowid
    return {"style_id": sid, "videos": len(allv), "new": len(videos), "failed": failed,
            "cuts_per_10s": agg["edit"].get("cuts_per_10s"),
            "sound_pro": bool(agg.get("sound_pro"))}


def list_styles() -> list[dict]:
    ensure_schema()
    with db.get_conn() as conn:
        rows = conn.execute("SELECT * FROM learned_styles ORDER BY id DESC").fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["profile"] = json.loads(d["profile"])
        d["style"] = json.loads(d["style"])
        out.append(d)
    return out


def style_for(style_id: int) -> dict:
    ensure_schema()
    with db.get_conn() as conn:
        r = conn.execute("SELECT style FROM learned_styles WHERE id = ?", (style_id,)).fetchone()
    return style_mod.clean_style(json.loads(r["style"]) if r else None)


def style_profile(style_id: int) -> dict | None:
    """Perfil completo del estilo aprendido, incluido el desarme frame-accurate (edl)."""
    ensure_schema()
    with db.get_conn() as conn:
        r = conn.execute("SELECT profile FROM learned_styles WHERE id = ?", (style_id,)).fetchone()
    return json.loads(r["profile"]) if r else None
