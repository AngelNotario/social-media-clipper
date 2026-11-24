"""Revisión preventiva ANTES de renderizar: detecta (y arregla lo seguro) lo que arruinaría el video.

- Gancho negro o casi negro en el primer segundo.
- Silencio al inicio de más de 0.5 s (el espectador se va antes de oír nada).
- Textos colgados: vacíos, que duran casi nada, que se salen del video o que se pisan con otro.
- Recursos (b-roll) que se salen del final.
- Duración final muy lejos de la que pediste en el brief.
Lo que se puede arreglar sin cambiar tu edición (recortar un texto al final, quitar uno vacío) se arregla en
la copia que se renderiza; lo demás se reporta para que decidas.
"""
from __future__ import annotations

import copy
import subprocess
from pathlib import Path

MIN_TEXT_S = 0.4
SILENCE_START_S = 0.5


def _first_luma(src: str, t: float) -> float | None:
    """Brillo medio (0-255) de un fotograma de la fuente."""
    try:
        r = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{max(0.0, t):.3f}", "-i", src, "-frames:v", "1",
                            "-vf", "scale=64:-2,format=gray", "-f", "rawvideo", "-"],
                           capture_output=True, timeout=30)
        data = r.stdout
        return sum(data) / len(data) if data else None
    except (OSError, subprocess.SubprocessError):
        return None


def check(p: dict, target_s: float | None = None) -> tuple[dict, dict]:
    """Devuelve (timeline listo para renderizar, reporte {issues, fixed})."""
    tl = copy.deepcopy(p["timeline"])
    dur = float(tl.get("duration") or 0)
    issues: list[dict] = []
    fixed: list[str] = []

    def issue(key: str, msg: str, level: str = "warn"):
        issues.append({"key": key, "level": level, "msg": msg})

    # 1) gancho negro
    segs = tl["tracks"]["video"]
    meta = p.get("source_meta") or {}
    if segs and p.get("source_kind") == "video" and meta.get("has_video", True) and Path(p.get("source_path") or "").exists():
        first = segs[0]
        covered = any(float(b.get("start", 9)) <= 0.2 and b.get("mode") in (None, "full", "cover")
                      for b in tl["tracks"].get("broll") or [])
        if not covered:
            lum = [x for x in (_first_luma(p["source_path"], float(first["src_in"]) + d) for d in (0.1, 0.6)) if x is not None]
            if lum and max(lum) < 18:
                issue("black_hook", "El video empieza en negro: el gancho no se ve. Recorta el inicio o pon un recurso.", "error")

    # 2) silencio al inicio
    words = tl.get("words") or []
    if words and meta.get("has_audio", True):
        w0 = float(words[0]["start"])
        if w0 > SILENCE_START_S:
            issue("silent_start", f"Hay {w0:.1f} s de silencio antes de la primera palabra: recórtalo para enganchar.")

    # 3) textos colgados
    texts = []
    for t in tl["tracks"].get("text") or []:
        s, e = float(t.get("start", 0)), float(t.get("end", 0))
        if not str(t.get("text") or "").strip():
            fixed.append("quité un texto vacío")
            continue
        if dur and s >= dur - 0.2:
            fixed.append(f"quité «{str(t['text'])[:24]}» (empezaba después del final)")
            continue
        if dur and e > dur:
            t["end"] = round(dur, 3)
            e = dur
            fixed.append(f"recorté «{str(t['text'])[:24]}» al final del video")
        if e - s < MIN_TEXT_S:
            fixed.append(f"quité «{str(t['text'])[:24]}» (duraba {max(0.0, e - s):.2f} s, no se alcanza a leer)")
            continue
        texts.append(t)
    tl["tracks"]["text"] = texts
    big = [t for t in texts if t.get("kind") in ("meme", "callout", "title")]
    big.sort(key=lambda t: float(t["start"]))
    for a, b in zip(big, big[1:]):
        if float(b["start"]) < float(a["end"]) - 0.05:
            issue("text_overlap", f"«{str(a['text'])[:20]}» y «{str(b['text'])[:20]}» salen al mismo tiempo y se pisan.")

    # 4) recursos que se salen
    for b in tl["tracks"].get("broll") or []:
        if dur and float(b.get("end", 0)) > dur:
            b["end"] = round(dur, 3)
            fixed.append("recorté un recurso que se salía del final")

    # 5) duración pedida
    if target_s and dur and abs(dur - float(target_s)) > max(5.0, 0.15 * float(target_s)):
        issue("duration", f"Dura {dur:.0f} s y pediste {float(target_s):.0f} s.")

    return tl, {"issues": issues, "fixed": fixed}
