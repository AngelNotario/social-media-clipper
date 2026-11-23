"""Transcripción con faster-whisper (GPU si está disponible, si no CPU)."""
from __future__ import annotations

import gc
from pathlib import Path
from typing import Callable

from . import gpu


def load_wav(path: Path):
    """WAV 16 kHz mono → float32 [-1, 1]. Evita depender de PyAV para decodificar."""
    import wave

    import numpy as np

    with wave.open(str(path), "rb") as w:
        if w.getframerate() != 16000 or w.getnchannels() != 1:
            raise RuntimeError("El audio debe ser WAV 16 kHz mono.")
        data = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
    return data.astype(np.float32) / 32768.0


def _run(wav: Path, model_name: str, language: str | None, device: str,
         duration: float | None, progress: Callable[[float], None]) -> dict:
    from faster_whisper import WhisperModel

    from . import vocab

    compute = "float16" if device == "cuda" else "int8"
    model = WhisperModel(model_name, device=device, compute_type=compute)
    try:
        kw = dict(language=language or None, word_timestamps=True, vad_filter=True, beam_size=5,
                  condition_on_previous_text=False)
        hot = vocab.hotwords()
        if hot:  # nombres de tu diccionario (Eneba, GTA 6…) para que no los escriba mal
            kw["hotwords"] = hot
        audio = load_wav(wav)
        try:
            segments_iter, info = model.transcribe(audio, **kw)
        except TypeError:  # faster-whisper antiguo sin hotwords: misma pista como texto inicial
            kw.pop("hotwords", None)
            if hot:
                kw["initial_prompt"] = hot
            segments_iter, info = model.transcribe(audio, **kw)
        total = duration or info.duration or 1
        segments = []
        for s in segments_iter:
            words = [
                {"start": round(w.start, 2), "end": round(w.end, 2), "word": w.word.strip()}
                for w in (s.words or []) if w.word.strip()
            ]
            segments.append({
                "start": round(s.start, 2), "end": round(s.end, 2),
                "text": s.text.strip(), "words": words,
            })
            progress(min(s.end / total, 1.0))
        return {"language": info.language, "model": model_name, "device": device, "segments": segments}
    finally:
        del model
        gc.collect()


def transcribe(wav: Path, model_name: str = "large-v3-turbo", language: str | None = "es",
               duration: float | None = None,
               progress: Callable[[float], None] = lambda p: None) -> dict:
    if gpu.cuda_available():
        try:
            return _run(wav, model_name, language, "cuda", duration, progress)
        except RuntimeError as exc:  # p. ej. falta una DLL de CUDA: seguimos en CPU
            if not any(k in str(exc).lower() for k in ("cuda", "cublas", "cudnn", "out of memory")):
                raise
    return _run(wav, model_name, language, "cpu", duration, progress)
