"""Control de calidad automático de un video renderizado (como la revisión final de un editor).

Una sola pasada de FFmpeg detecta: pantallas negras, imagen congelada, silencios (ritmo muerto),
saturación o volumen bajo y duración incorrecta. Con los subtítulos revisa que existan y que la
primera frase llegue pronto (el gancho de los primeros 3 s).
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

from ..ai import gpu

HOOK = 3.0


def _scan(path: Path, silence_db: float, silence_d: float) -> str:
    cmd = ["ffmpeg", "-hide_banner", "-nostats"]
    if gpu.nvenc_available():
        cmd += ["-hwaccel", "cuda"]
    cmd += ["-i", str(path),
            "-vf", "scale=320:-2,blackdetect=d=0.3:pix_th=0.10,freezedetect=n=-60dB:d=2",
            "-af", f"silencedetect=n={silence_db}dB:d={silence_d},volumedetect",
            "-f", "null", "-"]
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=3600)
    if r.returncode != 0 and "-hwaccel" in cmd:  # sin decodificación por GPU: repetir en CPU
        cmd.remove("-hwaccel"), cmd.remove("cuda")
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=3600)
    return r.stderr


def parse(log: str) -> dict:
    num = r"(-?[\d.]+|-?inf)"
    black = [(float(a), float(c)) for a, _, c in
             re.findall(rf"black_start:{num} black_end:{num} black_duration:{num}", log)]
    fstarts = [float(x) for x in re.findall(rf"freeze_start: {num}", log)]
    fdurs = [float(x) for x in re.findall(rf"freeze_duration: {num}", log)]
    freeze = list(zip(fstarts, fdurs))
    if len(fstarts) > len(fdurs):  # congelado hasta el final: sin duración reportada
        freeze.append((fstarts[-1], -1.0))
    sstarts = [float(x) for x in re.findall(rf"silence_start: {num}", log)]
    sdurs = [float(x) for x in re.findall(rf"silence_duration: {num}", log)]
    silence = list(zip(sstarts, sdurs))
    if len(sstarts) > len(sdurs):
        silence.append((sstarts[-1], -1.0))
    mean = re.search(rf"mean_volume: {num} dB", log)
    peak = re.search(rf"max_volume: {num} dB", log)
    return {"black": black, "freeze": freeze, "silence": silence,
            "mean_db": float(mean.group(1)) if mean else None, "max_db": float(peak.group(1)) if peak else None}


def evaluate(scan: dict, duration: float, expected: float | None = None, *, kind: str = "clip",
             captions: bool = False, words: list[dict] | None = None, has_audio: bool = True) -> dict:
    """Convierte las detecciones en problemas con gravedad y una nota de 0 a 100."""
    issues: list[dict] = []

    def add(sev: str, typ: str, msg: str, t: float | None = None, pen: int = 0) -> None:
        issues.append({"severity": sev, "type": typ, "message": msg,
                       "t": round(t, 2) if t is not None else None, "penalty": pen})

    for t, d in scan["black"]:
        if t >= duration - 0.6:  # fundido a negro al final: normal
            continue
        if t < HOOK:
            add("alta", "negro", f"Pantalla negra de {d:.1f} s en el gancho", t, 25)
        elif d >= 0.8:
            add("media", "negro", f"Pantalla negra de {d:.1f} s", t, 10)
    for t, d in scan["freeze"]:
        d = (duration - t) if d < 0 else d
        add("alta" if d > 4 else "media", "congelado", f"Imagen congelada {d:.1f} s", t, 15 if d > 4 else 8)
    if has_audio:
        long_gap = 2.0 if kind == "clip" else 4.0
        for t, d in scan["silence"]:
            d = (duration - t) if d < 0 else d
            if t < HOOK - 0.5 and kind == "clip":
                add("alta", "silencio", f"El clip empieza con {d:.1f} s de silencio (el espectador se va)", t, 20)
            elif d >= long_gap:
                add("media", "silencio", f"Silencio de {d:.1f} s: ritmo muerto, conviene cortarlo", t, 8)
        if scan["max_db"] is not None and scan["max_db"] >= -0.1:
            add("media", "saturado", "El audio llega al máximo y puede sonar saturado", None, 6)
        if scan["mean_db"] is not None and scan["mean_db"] < -32:
            add("media", "volumen", f"Volumen bajo (media {scan['mean_db']:.0f} dB)", None, 8)
    if expected and abs(duration - expected) > 0.6:
        add("alta", "duracion", f"Dura {duration:.1f} s y se esperaban {expected:.1f} s", None, 20)
    if captions:
        if not words:
            add("alta", "subtitulos", "Pediste subtítulos pero el clip no tiene palabras transcritas", None, 20)
        elif kind == "clip" and words[0]["start"] > 2.5:
            add("media", "gancho", f"La primera frase llega a los {words[0]['start']:.1f} s; el gancho debe hablar ya",
                words[0]["start"], 8)
    if kind == "clip" and duration > 90:
        add("baja", "duracion", "Pasa de 90 s: en Shorts y Reels rinde mejor por debajo de 60 s", None, 3)

    score = max(0, 100 - sum(i["penalty"] for i in issues))
    verdict = "listo" if score >= 85 else "revisar" if score >= 60 else "rehacer"
    order = {"alta": 0, "media": 1, "baja": 2}
    issues.sort(key=lambda i: (order[i["severity"]], i["t"] or 0))
    return {"score": score, "verdict": verdict, "issues": issues,
            "audio": {"mean_db": scan["mean_db"], "max_db": scan["max_db"]}}


def check(path: Path, duration: float, expected: float | None = None, *, kind: str = "clip",
          captions: bool = False, words: list[dict] | None = None, has_audio: bool = True) -> dict:
    sil_db, sil_d = (-45, 1.0) if kind == "clip" else (-45, 2.5)
    try:
        scan = parse(_scan(path, sil_db, sil_d))
    except (subprocess.TimeoutExpired, OSError) as exc:
        return {"score": None, "verdict": "sin revisar", "issues": [], "error": str(exc)[:200]}
    return evaluate(scan, duration, expected, kind=kind, captions=captions, words=words, has_audio=has_audio)
