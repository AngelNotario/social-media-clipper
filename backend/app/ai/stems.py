"""Separación de voz y música (Demucs) y ritmo musical (librosa), vía audio_worker.py en .venv-tts.

- timeline_stems: separa solo los tramos que usa la edición (en bloques, con caché) y arma pistas
  alineadas con el timeline final: voz sola, resto (música/juego) y la mezcla elegida.
  Sirve para: ducking real (la música baja solo cuando hablas), realzar la voz sobre el juego
  y quitar la música de fondo del original (evita reclamos de copyright).
- beats_for: tempo y beats de una canción para sincronizar zooms, textos y efectos con el ritmo.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import wave
from pathlib import Path
from typing import Callable

import numpy as np

from .. import config, db

WORKER = Path(__file__).resolve().parents[2] / "audio_worker.py"
CACHE = config.DATA_DIR / "cache"
SR = 44100
MAX_SEPARATE = 3600.0  # segundos máximos a separar por render
VOICE_MODES = ("original", "realzar", "solo_voz")
_available: bool | None = None


def _py() -> Path | None:
    from ..studio.voice import tts_python
    return tts_python()


def available() -> bool:
    """¿Está Demucs instalado en el entorno de PyTorch?"""
    global _available
    if _available is None:
        py = _py()
        try:
            _available = bool(py) and subprocess.run([str(py), "-c", "import demucs, librosa"],
                                                     capture_output=True, timeout=120).returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            _available = False
    return _available


def _run(args: list[str], timeout: float) -> dict:
    r = subprocess.run([str(_py()), str(WORKER), *args], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=timeout)
    for line in reversed(r.stdout.strip().splitlines()):
        try:
            return json.loads(line)
        except json.JSONDecodeError:
            continue
    return {"ok": False, "error": (r.stderr or r.stdout).strip()[-300:]}


def _sig(path: Path, *extra) -> str:
    st = path.stat()
    raw = "|".join([str(path.resolve()), str(st.st_size), str(int(st.st_mtime))] + [str(x) for x in extra])
    return hashlib.sha1(raw.encode()).hexdigest()[:16]


def blocks(ranges: list[tuple[float, float]], gap: float = 4.0, pad: float = 0.5) -> list[tuple[float, float]]:
    """Une tramos cercanos en bloques (menos llamadas a Demucs, sin cortes en los bordes)."""
    out: list[list[float]] = []
    for a, b in sorted((max(0.0, a - pad), b + pad) for a, b in ranges if b > a):
        if out and a <= out[-1][1] + gap:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return [(round(a, 3), round(b, 3)) for a, b in out]


def _free_vram() -> None:
    from . import llm
    url, model = db.get_setting("ollama_url"), db.get_setting("ollama_model")
    if url and model:
        llm.unload(url, model)


def separate(src: Path, a: float, b: float, track: int | None = None) -> Path | None:
    """Separa src[a:b] en voz y resto. Devuelve la carpeta con vocals.wav y accomp.wav (en caché)."""
    out = CACHE / "stems" / _sig(src, f"{a:.3f}", f"{b:.3f}", track)
    if (out / "vocals.wav").exists() and (out / "accomp.wav").exists():
        return out
    out.mkdir(parents=True, exist_ok=True)
    wav = out / "input.wav"
    mapping = ["-map", f"0:a:{track}"] if track is not None else ["-map", "0:a:0"]
    r = subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", f"{a:.3f}", "-t", f"{b - a:.3f}", "-i", str(src),
                        *mapping, "-vn", "-ac", "2", "-ar", str(SR), "-c:a", "pcm_s16le", str(wav)],
                       capture_output=True, text=True, timeout=1800)
    if r.returncode != 0 or not wav.exists():
        return None
    res = _run(["stems", str(wav), str(out)], timeout=max(600.0, (b - a) * 3))
    wav.unlink(missing_ok=True)
    if not res.get("ok"):
        raise RuntimeError(f"Demucs falló: {res.get('error', '')}")
    (out / "info.json").write_text(json.dumps(res), encoding="utf-8")
    return out


def _read(path: Path, start: float, dur: float) -> np.ndarray:
    with wave.open(str(path), "rb") as w:
        n = w.getnframes()
        i0 = min(max(0, int(round(start * SR))), n)
        cnt = max(0, min(int(round(dur * SR)), n - i0))
        w.setpos(i0)
        data = np.frombuffer(w.readframes(cnt), dtype=np.int16).reshape(-1, 2).astype(np.float32) / 32768.0
    need = int(round(dur * SR))
    if len(data) < need:
        data = np.vstack([data, np.zeros((need - len(data), 2), np.float32)])
    return data[:need]


def _write(path: Path, data: np.ndarray) -> None:
    pcm = (np.clip(data, -1.0, 1.0) * 32767.0).astype(np.int16)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.tobytes())


def remix(vocals: np.ndarray, accomp: np.ndarray, mode: str) -> np.ndarray:
    if mode == "solo_voz":
        return vocals
    if mode == "realzar":  # voz al frente, el juego/música detrás (-5 dB)
        return vocals * 1.12 + accomp * 0.56
    return vocals + accomp


def timeline_stems(src: Path, segs: list[tuple[float, float]], work: Path, mode: str = "original",
                   track: int | None = None, progress: Callable[[float, str], None] = lambda p, s: None) -> dict | None:
    """Pistas alineadas con el timeline (segs = tramos del original en orden de aparición)."""
    if not available() or not segs:
        return None
    blks = blocks(segs)
    total = sum(b - a for a, b in blks)
    if total > MAX_SEPARATE:
        return {"error": f"demasiado audio para separar ({total / 60:.0f} min)"}
    _free_vram()  # Demucs en GPU necesita VRAM que Ollama puede estar ocupando
    dirs = []
    done = 0.0
    for a, b in blks:
        progress(done / total, f"Separando voz y música ({int(done)}/{int(total)} s)")
        dirs.append(((a, b), separate(src, a, b, track)))
        done += b - a
    voc, acc = [], []
    fade = int(0.012 * SR)
    ramp = np.linspace(0.0, 1.0, fade, dtype=np.float32)[:, None]
    for s0, s1 in segs:
        (a, _), d = next(((r, d) for r, d in dirs if r[0] <= s0 + 1e-3 and s1 <= r[1] + 1e-3), (None, None))
        if d is None:
            return {"error": "tramo fuera de los bloques separados"}
        v = _read(d / "vocals.wav", s0 - a, s1 - s0)
        m = _read(d / "accomp.wav", s0 - a, s1 - s0)
        if len(v) > 2 * fade:  # micro fundidos en los cortes (sin clics)
            for x in (v, m):
                x[:fade] *= ramp
                x[-fade:] *= ramp[::-1]
        voc.append(v)
        acc.append(m)
    vocals, accomp = np.vstack(voc), np.vstack(acc)
    work.mkdir(parents=True, exist_ok=True)
    out = {"vocals": work / "stem_vocals.wav", "mix": None, "mode": mode,
           "device": json.loads((dirs[0][1] / "info.json").read_text()).get("device") if (dirs[0][1] / "info.json").exists() else None,
           "voice_ratio": round(float(np.sqrt(np.mean(vocals ** 2)) / (np.sqrt(np.mean((vocals + accomp) ** 2)) + 1e-9)), 2)}
    _write(out["vocals"], vocals)
    if mode in ("realzar", "solo_voz"):
        out["mix"] = work / "stem_mix.wav"
        _write(out["mix"], remix(vocals, accomp, mode))
    return out


def public(info: dict | None) -> dict | None:
    """Resumen para guardar/mostrar (sin rutas temporales)."""
    if not info:
        return None
    if info.get("error"):
        return {"error": info["error"]}
    return {"mode": info.get("mode"), "device": info.get("device"), "voice_ratio": info.get("voice_ratio")}


# ---------------- ritmo ----------------

def beats_for(path: Path) -> dict | None:
    """Tempo y beats de una canción (en caché)."""
    if not available() or not path.exists():
        return None
    out = CACHE / "beats" / f"{_sig(path)}.json"
    if not out.exists():
        out.parent.mkdir(parents=True, exist_ok=True)
        res = _run(["beats", str(path), str(out)], timeout=300)
        if not res.get("ok") or not out.exists():
            return None
    return json.loads(out.read_text(encoding="utf-8"))


def beat_grid(info: dict, start: float, end: float, strong: bool = False) -> list[float]:
    """Beats en tiempo del timeline para música que empieza en `start` y se repite en bucle hasta `end`."""
    base = info.get("strong" if strong else "beats") or []
    dur = float(info.get("duration") or 0)
    if not base or dur <= 1:
        return []
    out, k = [], 0
    while start + k * dur < end:
        out += [start + k * dur + b for b in base if start + k * dur + b < end]
        k += 1
    return out
