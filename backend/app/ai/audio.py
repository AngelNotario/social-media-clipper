"""Audio (extracción, energía) y cortes de escena con FFmpeg."""
from __future__ import annotations

import re
import subprocess
import wave
from pathlib import Path

import numpy as np

ENERGY_HOP = 0.5  # segundos por muestra de la curva de energía


def audio_tracks(src: Path) -> int:
    """Número de pistas de audio (OBS puede grabar micrófono y juego por separado)."""
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "stream=index",
                        "-of", "csv=p=0", str(src)], capture_output=True, text=True, timeout=60)
    return len([x for x in r.stdout.split() if x.strip()])


def extract_wav(src: Path, dest: Path, track: int | None = None) -> None:
    """Audio mono 16 kHz. track = índice de pista de audio (None = la primera / mezcla)."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    mapping = ["-map", f"0:a:{track}"] if track is not None else []
    r = subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-i", str(src), *mapping, "-vn", "-ac", "1", "-ar", "16000",
         "-c:a", "pcm_s16le", str(dest)],
        capture_output=True, text=True,
    )
    if r.returncode != 0 or not dest.exists():
        raise RuntimeError(f"No se pudo extraer el audio: {r.stderr.strip()[-300:]}")


def energy_curve(wav_path: Path, hop: float = ENERGY_HOP) -> list[float]:
    """Volumen relativo (0-1) cada `hop` segundos, normalizado por percentiles."""
    with wave.open(str(wav_path), "rb") as w:
        sr = w.getframerate()
        data = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32)
    n = int(sr * hop)
    if n <= 0 or data.size < n:
        return []
    frames = data[: (data.size // n) * n].reshape(-1, n)
    rms = np.sqrt(np.mean(frames ** 2, axis=1)) + 1e-6
    db = 20 * np.log10(rms)
    lo, hi = np.percentile(db, 5), np.percentile(db, 98)
    if hi - lo < 1e-3:
        return [0.5] * len(db)
    return [round(float(x), 3) for x in np.clip((db - lo) / (hi - lo), 0, 1)]


def detect_scenes(src: Path, threshold: float = 0.35) -> list[float]:
    """Tiempos (s) de cambios de plano. Rápido: analiza 4 fps a baja resolución."""
    r = subprocess.run(
        ["ffmpeg", "-hide_banner", "-v", "info", "-i", str(src), "-an",
         "-vf", f"fps=4,scale=320:-2,select='gt(scene\\,{threshold})',showinfo",
         "-f", "null", "-"],
        capture_output=True, text=True, timeout=3600,
    )
    times = [float(m) for m in re.findall(r"pts_time:([\d.]+)", r.stderr)]
    return sorted({round(t, 2) for t in times})
