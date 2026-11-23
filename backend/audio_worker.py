"""Proceso auxiliar de audio (se ejecuta con el entorno .venv-tts, que tiene PyTorch con CUDA).

  python audio_worker.py stems <entrada.wav> <carpeta_salida>   → vocals.wav + accomp.wav (Demucs htdemucs)
  python audio_worker.py beats <audio> <salida.json>            → tempo y tiempos de cada beat (librosa)

Imprime una línea JSON con el resultado. Se ejecuta aparte para que PyTorch no ocupe memoria del servidor.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path


def stems(src: Path, out: Path) -> dict:
    import numpy as np
    import soundfile as sf
    import torch
    from demucs.apply import apply_model
    from demucs.pretrained import get_model

    t0 = time.time()
    wav, sr = sf.read(str(src), dtype="float32", always_2d=True)  # (T, ch)
    model = get_model("htdemucs")
    model.eval()
    if sr != model.samplerate:
        raise SystemExit(f"Frecuencia {sr} distinta de {model.samplerate}")
    x = torch.from_numpy(wav.T.copy())
    if x.shape[0] == 1:
        x = x.repeat(2, 1)
    ref = x.mean(0)
    mean, std = ref.mean(), ref.std() + 1e-8
    x = (x - mean) / std

    def run(device: str):
        with torch.no_grad():
            return apply_model(model, x[None], device=device, shifts=0, split=True, overlap=0.25,
                               progress=False)[0]

    device = "cuda" if torch.cuda.is_available() else "cpu"
    try:
        res = run(device)
    except RuntimeError as exc:  # sin VRAM libre (p. ej. Ollama cargado): CPU
        if device != "cuda" or "out of memory" not in str(exc).lower():
            raise
        torch.cuda.empty_cache()
        device = "cpu"
        res = run(device)
    res = res * std + mean
    names = model.sources
    vocals = res[names.index("vocals")]
    accomp = sum(res[i] for i, n in enumerate(names) if n != "vocals")
    out.mkdir(parents=True, exist_ok=True)
    sf.write(str(out / "vocals.wav"), vocals.T.cpu().numpy(), sr, subtype="PCM_16")
    sf.write(str(out / "accomp.wav"), accomp.T.cpu().numpy(), sr, subtype="PCM_16")
    return {"ok": True, "device": device, "seconds": round(time.time() - t0, 1),
            "duration": round(wav.shape[0] / sr, 2)}


def beats(src: Path, out: Path) -> dict:
    import librosa
    import numpy as np

    y, sr = librosa.load(str(src), sr=22050, mono=True)
    tempo, frames = librosa.beat.beat_track(y=y, sr=sr, units="frames")
    times = librosa.frames_to_time(frames, sr=sr)
    onset = librosa.onset.onset_strength(y=y, sr=sr)
    strength = onset[np.clip(frames, 0, len(onset) - 1)] if len(frames) else np.array([])
    data = {"tempo": float(np.atleast_1d(tempo)[0]), "duration": round(len(y) / sr, 3),
            "beats": [round(float(t), 3) for t in times],
            # beats fuertes: los de mayor golpe (sirven para cortes y zooms grandes)
            "strong": [round(float(t), 3) for t, s in zip(times, strength) if s >= np.percentile(strength, 70)]
            if len(strength) else []}
    out.write_text(json.dumps(data), encoding="utf-8")
    return {"ok": True, "tempo": data["tempo"], "beats": len(data["beats"])}


if __name__ == "__main__":
    mode, a, b = sys.argv[1], Path(sys.argv[2]), Path(sys.argv[3])
    try:
        result = stems(a, b) if mode == "stems" else beats(a, b)
    except Exception as exc:  # noqa: BLE001
        result = {"ok": False, "error": f"{type(exc).__name__}: {exc}"[:400]}
    print(json.dumps(result), flush=True)
