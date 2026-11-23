"""Tu voz clonada (Chatterbox Multilingual, local, licencia MIT) con emociones.

- El perfil se crea a partir de una grabación tuya (30 s o más).
- Si la grabación incluye las secciones emocionales de tu guion ([Alegría], [Sorpresa]...), se recortan
  referencias por emoción: así la voz clonada imita cómo TÚ suenas en cada emoción.
- Cada emoción ajusta además la intensidad expresiva del modelo y el ritmo/volumen final.
"""
from __future__ import annotations

import json
import re
import subprocess
import unicodedata
from pathlib import Path
from typing import Callable

from .. import config, db
from ..ai import llm

VOICE_DIR = config.DATA_DIR / "voice"
BACKEND_DIR = Path(__file__).resolve().parents[2]
TTS_PY_CANDIDATES = [BACKEND_DIR / ".venv-tts" / "Scripts" / "python.exe", BACKEND_DIR / ".venv-tts" / "bin" / "python"]

# exaggeration: intensidad expresiva · cfg: apego al ritmo de la referencia (bajo = más libre/rápido)
EMOTIONS = {
    "neutral":    {"label": "Neutral",    "exaggeration": 0.45, "cfg_weight": 0.50, "temperature": 0.80, "speed": 1.00, "gain": 0.0},
    "alegria":    {"label": "Alegría",    "exaggeration": 0.70, "cfg_weight": 0.45, "temperature": 0.85, "speed": 1.03, "gain": 1.0},
    "entusiasmo": {"label": "Entusiasmo", "exaggeration": 0.75, "cfg_weight": 0.40, "temperature": 0.85, "speed": 1.06, "gain": 1.5},
    "sorpresa":   {"label": "Sorpresa",   "exaggeration": 0.80, "cfg_weight": 0.40, "temperature": 0.85, "speed": 1.02, "gain": 1.5},
    "enojo":      {"label": "Enojo",      "exaggeration": 0.82, "cfg_weight": 0.42, "temperature": 0.80, "speed": 1.05, "gain": 2.5},
    "seriedad":   {"label": "Seriedad",   "exaggeration": 0.35, "cfg_weight": 0.60, "temperature": 0.70, "speed": 0.97, "gain": 0.0},
    "confianza":  {"label": "Confianza",  "exaggeration": 0.55, "cfg_weight": 0.50, "temperature": 0.80, "speed": 1.00, "gain": 0.5},
    "calma":      {"label": "Calma",      "exaggeration": 0.25, "cfg_weight": 0.60, "temperature": 0.70, "speed": 0.94, "gain": -1.0},
    "reflexion":  {"label": "Reflexión",  "exaggeration": 0.30, "cfg_weight": 0.55, "temperature": 0.75, "speed": 0.95, "gain": -0.5},
    "tristeza":   {"label": "Tristeza",   "exaggeration": 0.40, "cfg_weight": 0.55, "temperature": 0.75, "speed": 0.91, "gain": -2.0},
    "miedo":      {"label": "Miedo/tensión", "exaggeration": 0.70, "cfg_weight": 0.42, "temperature": 0.80, "speed": 1.04, "gain": -0.5},
}
# Emoción → referencia de tu grabación que mejor la representa (si existe)
REF_FALLBACK = {"entusiasmo": "alegria", "enojo": "seriedad", "miedo": "sorpresa", "tristeza": "reflexion",
                "calma": "calma", "confianza": "confianza"}

# Frases de tu guion de clonación que marcan cada emoción
SCRIPT_MARKERS = {
    "neutral": "hola que tal mi nombre es",
    "alegria": "no sabes lo feliz que estoy",
    "calma": "respira profundamente no hay ninguna prisa",
    "sorpresa": "espera un momento me estas diciendo",
    "seriedad": "quiero que entiendas algo cada decision",
    "entusiasmo": "vamos a hacerlo este es el momento perfecto",
    "confianza": "estoy convencido de que podemos conseguirlo",
    "reflexion": "a veces lo mas importante no es llegar rapido",
}


def _norm(text: str) -> str:
    t = unicodedata.normalize("NFKD", text.lower()).encode("ascii", "ignore").decode()
    return " ".join(re.sub(r"[^a-z0-9 ]+", " ", t).split())


def tts_python() -> Path | None:
    return next((p for p in TTS_PY_CANDIDATES if p.exists()), None)


_tts_ok: bool | None = None


def tts_available() -> bool:
    global _tts_ok
    if _tts_ok is None:
        py = tts_python()
        if not py:
            _tts_ok = False
        else:
            r = subprocess.run([str(py), "-c", "import chatterbox.mtl_tts, soundfile"], capture_output=True, timeout=120)
            _tts_ok = r.returncode == 0
    return _tts_ok


def profile() -> dict | None:
    f = VOICE_DIR / "perfil.json"
    if not f.exists():
        return None
    p = json.loads(f.read_text(encoding="utf-8"))
    p["available"] = Path(p["ref_main"]).exists()
    return p


def _ffmpeg(args: list[str]) -> None:
    r = subprocess.run(["ffmpeg", "-y", "-v", "error", *args], capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip()[-300:])


def _cut(src: Path, start: float, end: float, dest: Path) -> None:
    _ffmpeg(["-ss", f"{start:.2f}", "-to", f"{end:.2f}", "-i", str(src), "-ac", "1", "-ar", "24000", str(dest)])


def create_profile(sample: Path, progress: Callable[[float, str], None] = lambda p, s: None) -> dict:
    """Prepara tu voz: limpia el audio, lo transcribe y recorta referencias (general y por emoción)."""
    from ..ai import transcribe
    from .probe import probe_any

    VOICE_DIR.mkdir(parents=True, exist_ok=True)
    clean = VOICE_DIR / "muestra_limpia.wav"
    progress(0.05, "Limpiando tu grabación")
    _ffmpeg(["-i", str(sample), "-ac", "1", "-ar", "24000",
             "-af", "highpass=f=70,afftdn=nf=-25,"
                    "silenceremove=start_periods=1:start_threshold=-45dB:stop_periods=-1:stop_duration=0.8:stop_threshold=-45dB,"
                    "loudnorm=I=-18:TP=-2:LRA=11",
             str(clean)])
    dur = probe_any(clean)["duration"] or 0
    if dur < 8:
        raise RuntimeError("La grabación es muy corta: necesito al menos 10 segundos de tu voz (ideal 30 s o más).")

    progress(0.15, "Transcribiendo tu grabación")
    wav16 = VOICE_DIR / "muestra16k.wav"
    _ffmpeg(["-i", str(clean), "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(wav16)])
    tr = transcribe.transcribe(wav16, db.get_setting("whisper_model"), "es", dur)
    wav16.unlink(missing_ok=True)
    words = [w for s in tr["segments"] for w in s.get("words", [])]
    segs = tr["segments"]

    # Referencia principal: el tramo continuo más cercano a 12 s desde el inicio de una frase
    progress(0.7, "Recortando referencias de tu voz")
    main_start, main_end = 0.0, min(dur, 14.0)
    best = None
    for i, s in enumerate(segs):
        j = i
        while j + 1 < len(segs) and segs[j + 1]["end"] - s["start"] <= 15:
            j += 1
        length = segs[j]["end"] - s["start"]
        score = -abs(length - 12)
        if best is None or score > best[0]:
            best = (score, s["start"], segs[j]["end"])
    if best and best[2] - best[1] >= 6:
        main_start, main_end = best[1], best[2]
    ref_main = VOICE_DIR / "ref_general.wav"
    _cut(clean, max(0, main_start - 0.1), min(dur, main_end + 0.2), ref_main)

    # Referencias por emoción (si leíste las secciones de tu guion)
    refs: dict[str, str] = {}
    norm_words = [(_norm(w["word"]) or "", w) for w in words]
    for emo, marker in SCRIPT_MARKERS.items():
        mk = marker.split()
        for k in range(len(norm_words) - len(mk) + 1):
            window = [norm_words[k + n][0] for n in range(len(mk))]
            hits = sum(1 for a, b in zip(window, mk) if a == b)
            if hits >= len(mk) - 1:
                start = norm_words[k][1]["start"]
                # hasta el final de esa frase/párrafo (máx. 9 s)
                end = start
                for _, w in norm_words[k:]:
                    if w["start"] - start > 9:
                        break
                    end = w["end"]
                    if re.search(r"[.!?]$", w["word"]) and end - start > 3:
                        break
                if end - start >= 2.5:
                    out = VOICE_DIR / f"ref_{emo}.wav"
                    _cut(clean, max(0, start - 0.1), min(dur, end + 0.25), out)
                    refs[emo] = str(out)
                break

    prof = {"ref_main": str(ref_main), "refs": refs, "duration": round(dur, 1),
            "transcript": " ".join(s["text"] for s in segs)[:2000], "sample": str(clean)}
    (VOICE_DIR / "perfil.json").write_text(json.dumps(prof, ensure_ascii=False, indent=1), encoding="utf-8")
    progress(1.0, "Perfil de voz listo")
    return prof


def unload_llms() -> None:
    """Libera la VRAM de Ollama antes de cargar la voz."""
    url = db.get_setting("ollama_url")
    for m in llm.status(url)["models"]:
        llm.unload(url, m)


def _run_worker(py: Path, items: list[dict], work: Path, progress: Callable[[float], None]) -> None:
    job = work / f"tts_job_{len(list(work.glob('tts_job_*.json')))}.json"
    job.write_text(json.dumps({"language": "es", "items": items}, ensure_ascii=False), encoding="utf-8")
    import os
    log = job.with_suffix(".log")
    env = {**os.environ, "TQDM_DISABLE": "1", "PYTHONIOENCODING": "utf-8"}
    # stderr va a un archivo: si fuera a una tubería sin leer, se llena y el proceso se bloquea
    with open(log, "w", encoding="utf-8", errors="replace") as errf:
        proc = subprocess.Popen([str(py), str(BACKEND_DIR / "tts_worker.py"), str(job)], stdout=subprocess.PIPE,
                                stderr=errf, text=True, encoding="utf-8", errors="replace", env=env)
        for out_line in proc.stdout:
            try:
                msg = json.loads(out_line)
            except json.JSONDecodeError:
                continue
            if "progress" in msg:
                progress(msg["progress"])
        proc.wait()
    if proc.returncode != 0:
        tail = log.read_text(encoding="utf-8", errors="replace").strip()[-600:]
        raise RuntimeError("Falló la síntesis de voz: " + tail)


def _tokens(text: str) -> list[str]:
    return _norm(text).split()


def verify_lines(paths: list[Path], texts: list[str]) -> list[dict]:
    """Transcribe cada frase generada y la compara con el texto esperado.

    score = fracción de palabras esperadas que aparecen en orden; cut_end = fin de la última palabra
    esperada si después hay audio de más (balbuceo).
    """
    import wave

    import numpy as np

    from ..ai import gpu
    gpu.setup_cuda_dlls()
    from faster_whisper import WhisperModel
    device = "cuda" if gpu.cuda_available() else "cpu"
    model = WhisperModel(db.get_setting("whisper_model"), device=device,
                         compute_type="float16" if device == "cuda" else "int8")
    out = []
    try:
        for p, text in zip(paths, texts):
            tmp = p.with_name(p.stem + "_16k.wav")
            _ffmpeg(["-i", str(p), "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(tmp)])
            with wave.open(str(tmp), "rb") as w:
                dur = w.getnframes() / 16000
                a = np.frombuffer(w.readframes(w.getnframes()), np.int16).astype(np.float32) / 32768
            tmp.unlink(missing_ok=True)
            segs, _ = model.transcribe(a, language="es", word_timestamps=True, vad_filter=False,
                                       condition_on_previous_text=False)
            words = [w for s in segs for w in (s.words or [])]
            exp = _tokens(text)
            got = [(_norm(w.word), w) for w in words]
            j, matched, last_end = 0, 0, None
            for e in exp:  # alineación en orden, tolerante a palabras sueltas
                for k in range(j, min(j + 4, len(got))):
                    if got[k][0] == e or (len(e) > 4 and got[k][0][:4] == e[:4]):
                        matched += 1
                        last_end = got[k][1].end
                        j = k + 1
                        break
            score = matched / max(len(exp), 1)
            extra = len(got) - j
            cut = None
            if last_end and score >= 0.6 and (extra >= 2 or dur - last_end > 1.2):
                cut = min(dur, last_end + 0.15)
            out.append({"score": round(score, 2), "cut_end": cut, "heard": " ".join(g[0] for g in got)[:200]})
    finally:
        del model
        import gc
        gc.collect()
    return out


def synthesize(lines: list[dict], work: Path, progress: Callable[[float, str], None] = lambda p, s: None,
               use_profile: bool = True) -> tuple[Path, list[dict]]:
    """lines = [{"text", "emotion"}] → (wav final, [{start, end, text, emotion}])."""
    py = tts_python()
    if not py or not tts_available():
        raise RuntimeError("El motor de voz (Chatterbox) no está instalado. Revisa la sección Mi voz.")
    work.mkdir(parents=True, exist_ok=True)
    prof = profile() if use_profile else None
    items = []
    for i, ln in enumerate(lines):
        emo = ln.get("emotion") if ln.get("emotion") in EMOTIONS else "neutral"
        e = EMOTIONS[emo]
        ref = None
        if prof and prof.get("available"):
            ref = prof["refs"].get(emo) or prof["refs"].get(REF_FALLBACK.get(emo, "")) or prof["ref_main"]
        items.append({"text": ln["text"].strip(), "out": str(work / f"linea_{i:03d}.wav"), "ref": ref,
                      "exaggeration": e["exaggeration"], "cfg_weight": e["cfg_weight"],
                      "temperature": e["temperature"], "seed": 1234 + i})
    unload_llms()
    progress(0.02, "Cargando tu voz")
    _run_worker(py, items, work, lambda p: progress(0.05 + 0.6 * p, f"Generando tu voz ({round(p * len(items))}/{len(items)} frases)"))

    # Verificación con Whisper: recorta balbuceos al final y regenera frases que salieron mal
    progress(0.68, "Verificando cada frase")
    checks = verify_lines([Path(it["out"]) for it in items], [ln["text"] for ln in lines])
    bad = [i for i, c in enumerate(checks) if c["score"] < 0.6]
    if bad:
        retry = []
        for i in bad:
            it = dict(items[i])
            it.update(exaggeration=max(0.3, it["exaggeration"] - 0.25), cfg_weight=0.55, temperature=0.7,
                      seed=it["seed"] + 101, out=str(work / f"linea_{i:03d}_r.wav"))
            retry.append(it)
        progress(0.72, f"Regenerando {len(retry)} frase(s) con voz más estable")
        _run_worker(py, retry, work, lambda p: None)
        rchecks = verify_lines([Path(it["out"]) for it in retry], [lines[i]["text"] for i in bad])
        for i, it, rc in zip(bad, retry, rchecks):
            if rc["score"] >= checks[i]["score"]:
                items[i] = it
                checks[i] = rc
    for it, c in zip(items, checks):
        if c.get("cut_end"):
            trimmed = Path(it["out"]).with_name(Path(it["out"]).stem + "_cut.wav")
            _ffmpeg(["-i", it["out"], "-t", f"{c['cut_end']:.3f}", "-af", "afade=t=out:st=%.3f:d=0.08" % max(0, c["cut_end"] - 0.08),
                     str(trimmed)])
            it["out"] = str(trimmed)

    # Post-proceso por emoción: recorte de silencios, ritmo, volumen; y unión con pausas naturales
    progress(0.92, "Ajustando ritmo y emoción")
    parts, timings, t = [], [], 0.0
    from .probe import probe_any
    for i, (ln, it) in enumerate(zip(lines, items)):
        emo = ln.get("emotion") if ln.get("emotion") in EMOTIONS else "neutral"
        e = EMOTIONS[emo]
        fixed = work / f"linea_{i:03d}_ok.wav"
        _ffmpeg(["-i", it["out"], "-af",
                 "silenceremove=start_periods=1:start_threshold=-48dB,areverse,"
                 "silenceremove=start_periods=1:start_threshold=-48dB,areverse,"
                 f"atempo={e['speed']},volume={e['gain']}dB,aresample=48000",
                 "-ac", "2", str(fixed)])
        d = probe_any(fixed)["duration"] or 0
        timings.append({"start": round(t, 3), "end": round(t + d, 3), "text": ln["text"], "emotion": emo})
        parts.append(fixed)
        gap = 0.28 if re.search(r"[.!?…]$", ln["text"].strip()) else 0.12
        t += d + gap
        silence = work / f"pausa_{i:03d}.wav"
        _ffmpeg(["-f", "lavfi", "-i", f"anullsrc=r=48000:cl=stereo", "-t", f"{gap}", str(silence)])
        parts.append(silence)
    lst = work / "lista.txt"
    lst.write_text("".join(f"file '{p.as_posix()}'\n" for p in parts), encoding="utf-8")
    out = work / "narracion.wav"
    _ffmpeg(["-f", "concat", "-safe", "0", "-i", str(lst), "-af", "loudnorm=I=-16:TP=-1.5:LRA=9", "-ar", "48000", str(out)])
    return out, timings
