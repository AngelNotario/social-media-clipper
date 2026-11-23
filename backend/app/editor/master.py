"""Masterización de audio: cadena de voz en el render y loudnorm de dos pasos sobre el archivo final.

Objetivo: el volumen que esperan TikTok / Reels / Shorts / YouTube (-14 LUFS, pico real -1.5 dBTP).
La segunda pasada solo recodifica el audio (el video se copia), así que es rápida.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

TARGET_I, TARGET_TP, TARGET_LRA = -14.0, -1.5, 11.0

# Cadena suave para audio mezclado (voz + juego): quita retumbe y empareja la dinámica sin "bombear".
VOICE_CHAIN = ("highpass=f=70,"
               "acompressor=threshold=-20dB:ratio=2.5:attack=8:release=160:makeup=1.5:knee=4")
# Para tomas de solo voz (micrófono): además reduce el ruido de fondo.
CLEAN_CHAIN = "highpass=f=80,afftdn=nf=-28:tn=1," + VOICE_CHAIN.split(",", 1)[1]


def chain(clean: bool = False) -> str:
    return CLEAN_CHAIN if clean else VOICE_CHAIN


def _loudnorm(extra: str = "") -> str:
    return f"loudnorm=I={TARGET_I}:TP={TARGET_TP}:LRA={TARGET_LRA}{extra}"


def measure(path: Path) -> dict | None:
    """Primera pasada: mide el volumen integrado, el pico real y el rango dinámico."""
    r = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(path), "-vn",
                        "-af", _loudnorm(":print_format=json"), "-f", "null", "-"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=1800)
    m = re.findall(r"\{[^{}]*\"input_i\"[^{}]*\}", r.stderr, re.S)
    if not m:
        return None
    try:
        data = json.loads(m[-1])
        return {k: float(data[k]) for k in ("input_i", "input_tp", "input_lra", "input_thresh", "target_offset")}
    except (ValueError, KeyError):
        return None


def normalize(path: Path, bitrate: str = "192k") -> dict:
    """Deja el archivo a -14 LUFS. Devuelve lo medido antes y después (o el motivo si se omitió)."""
    m = measure(path)
    if not m or m["input_i"] < -70:  # sin audio útil (silencio): nada que normalizar
        return {"applied": False, "reason": "sin audio"}
    if abs(m["input_i"] - TARGET_I) < 0.6 and m["input_tp"] <= TARGET_TP + 0.2:
        return {"applied": False, "reason": "ya estaba en volumen", "lufs": round(m["input_i"], 1)}
    ln = _loudnorm(f":measured_I={m['input_i']}:measured_TP={m['input_tp']}:measured_LRA={m['input_lra']}"
                   f":measured_thresh={m['input_thresh']}:offset={m['target_offset']}:linear=true")
    tmp = path.with_name(path.stem + ".master" + path.suffix)
    r = subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(path), "-map", "0:v?", "-map", "0:a:0",
                        "-c:v", "copy", "-af", ln, "-c:a", "aac", "-b:a", bitrate, "-ar", "48000",
                        "-movflags", "+faststart", str(tmp)],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=1800)
    if r.returncode != 0 or not tmp.exists():
        tmp.unlink(missing_ok=True)
        return {"applied": False, "reason": "falló la normalización", "error": r.stderr.strip()[-200:]}
    os.replace(tmp, path)
    after = measure(path)
    return {"applied": True, "before_lufs": round(m["input_i"], 1),
            "lufs": round(after["input_i"], 1) if after else TARGET_I,
            "true_peak": round(after["input_tp"], 1) if after else None}
