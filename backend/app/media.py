"""Utilidades de video basadas en FFmpeg / FFprobe."""
from __future__ import annotations

import json
import mimetypes
import shutil
import subprocess
from fractions import Fraction
from pathlib import Path

from . import config


class MediaError(Exception):
    pass


def ffmpeg_available() -> bool:
    return bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))


def ffmpeg_version() -> str | None:
    if not shutil.which("ffmpeg"):
        return None
    try:
        out = subprocess.run(
            ["ffmpeg", "-version"], capture_output=True, text=True, timeout=10
        ).stdout
        return out.splitlines()[0] if out else None
    except Exception:
        return None


def _fps(rate: str | None) -> float | None:
    if not rate or rate in ("0/0", "0"):
        return None
    try:
        return round(float(Fraction(rate)), 3)
    except (ValueError, ZeroDivisionError):
        return None


def probe(path: Path) -> dict:
    """Devuelve metadatos reales del archivo. Lanza MediaError si no es un video válido."""
    if not ffmpeg_available():
        raise MediaError("FFmpeg/FFprobe no está instalado o no está en el PATH.")
    try:
        proc = subprocess.run(
            [
                "ffprobe", "-v", "error", "-print_format", "json",
                "-show_format", "-show_streams", str(path),
            ],
            capture_output=True, text=True, timeout=60,
        )
    except subprocess.TimeoutExpired as exc:
        raise MediaError("FFprobe tardó demasiado en analizar el archivo.") from exc
    if proc.returncode != 0:
        raise MediaError("FFprobe no pudo leer el archivo (¿está dañado o no es un video?).")

    data = json.loads(proc.stdout or "{}")
    streams = data.get("streams", [])
    v = next(
        (s for s in streams
         if s.get("codec_type") == "video"
         and not s.get("disposition", {}).get("attached_pic")),
        None,
    )
    a = next((s for s in streams if s.get("codec_type") == "audio"), None)
    if v is None:
        raise MediaError("El archivo no contiene una pista de video.")

    fmt = data.get("format", {})
    duration = fmt.get("duration") or v.get("duration")
    width, height = v.get("width"), v.get("height")
    # Rotación (videos de celular grabados en vertical)
    rotation = 0
    for sd in v.get("side_data_list", []) or []:
        if "rotation" in sd:
            rotation = int(sd["rotation"])
    if v.get("tags", {}).get("rotate"):
        rotation = int(v["tags"]["rotate"])
    if abs(rotation) % 180 == 90 and width and height:
        width, height = height, width

    return {
        "duration": round(float(duration), 3) if duration else None,
        "width": width,
        "height": height,
        "fps": _fps(v.get("avg_frame_rate")) or _fps(v.get("r_frame_rate")),
        "video_codec": v.get("codec_name"),
        "audio_codec": a.get("codec_name") if a else None,
        "has_audio": a is not None,
        "size_bytes": int(fmt["size"]) if fmt.get("size") else path.stat().st_size,
    }


def make_thumbnail(src: Path, dest: Path, duration: float | None) -> bool:
    """Extrae un fotograma representativo como JPG. Devuelve True si lo logró."""
    if not ffmpeg_available():
        return False
    seek = 0.0
    if duration:
        seek = min(duration * 0.1, 10.0)
    dest.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg", "-y", "-v", "error", "-ss", f"{seek:.2f}", "-i", str(src),
        "-frames:v", "1", "-vf", "scale=480:-2", "-q:v", "4", str(dest),
    ]
    try:
        subprocess.run(cmd, capture_output=True, timeout=60)
    except subprocess.TimeoutExpired:
        return False
    if not dest.exists() and seek > 0:  # videos muy cortos: reintentar desde el inicio
        cmd[cmd.index("-ss") + 1] = "0"
        subprocess.run(cmd, capture_output=True, timeout=60)
    return dest.exists() and dest.stat().st_size > 0


def guess_mime(path: Path) -> str:
    ext = path.suffix.lower()
    if ext in config.MIME_OVERRIDES:
        return config.MIME_OVERRIDES[ext]
    return mimetypes.guess_type(str(path))[0] or "application/octet-stream"
