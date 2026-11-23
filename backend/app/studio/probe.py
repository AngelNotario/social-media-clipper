"""FFprobe para cualquier medio (video, audio o imagen)."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path


class ProbeError(Exception):
    pass


def probe_any(path: Path) -> dict:
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)],
        capture_output=True, text=True, timeout=60,
    )
    if r.returncode != 0:
        raise ProbeError("No se pudo leer el archivo (¿formato no compatible o dañado?).")
    data = json.loads(r.stdout or "{}")
    streams = data.get("streams", [])
    v = next((s for s in streams if s.get("codec_type") == "video"
              and not s.get("disposition", {}).get("attached_pic")), None)
    a = next((s for s in streams if s.get("codec_type") == "audio"), None)
    fmt = data.get("format", {})
    dur = fmt.get("duration") or (v or {}).get("duration") or (a or {}).get("duration")
    width, height = (v or {}).get("width"), (v or {}).get("height")
    rotation = 0
    for sd in (v or {}).get("side_data_list", []) or []:
        if "rotation" in sd:
            rotation = int(sd["rotation"])
    if abs(rotation) % 180 == 90 and width and height:
        width, height = height, width
    is_image = v is not None and (fmt.get("format_name", "").startswith(("image2", "png_pipe", "jpeg_pipe", "webp_pipe"))
                                  or not dur or float(dur or 0) < 0.05)
    return {
        "has_video": v is not None and not is_image,
        "is_image": is_image,
        "has_audio": a is not None,
        "duration": round(float(dur), 3) if dur and not is_image else None,
        "width": width, "height": height,
    }
