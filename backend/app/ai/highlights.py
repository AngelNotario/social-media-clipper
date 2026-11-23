"""Selección de los mejores momentos de un video.

Flujo:
1. La transcripción se divide en bloques de ~8 min.
2. El LLM local (Ollama) propone momentos por bloque y los califica con los criterios editoriales.
3. Los tiempos se ajustan a frases completas (segmentos/palabras de Whisper) y a cortes de escena.
4. Se combina con la energía del audio, se eliminan solapamientos y se devuelven los N mejores.

Si Ollama no está disponible se usa un método heurístico (energía + ritmo + señales de texto)
y se marca como tal: nunca se presenta como resultado de IA.
"""
from __future__ import annotations

import re
from typing import Callable

from . import llm
from .audio import ENERGY_HOP

CRITERIA = {
    "hook": 0.25,       # capacidad de captar atención en los primeros segundos
    "contexto": 0.15,   # se entiende sin ver el resto del video
    "emocion": 0.15,    # sorpresa, humor, curiosidad, tensión
    "valor": 0.15,      # información o aprendizaje
    "narrativa": 0.10,  # tiene inicio, desarrollo y cierre
    "retencion": 0.20,  # invita a verlo completo
}

CRITERIA_LABELS = {
    "hook": "Hook", "contexto": "Contexto", "emocion": "Emoción", "valor": "Valor",
    "narrativa": "Narrativa", "retencion": "Retención",
    "energia": "Energía del audio", "ritmo": "Ritmo", "senales": "Señales de texto",
    "seleccion": "Nota de selección", "juez": "Juez de retención", "reglas": "Medición (voz, ritmo, cierre)",
}

MOMENT_SCHEMA = {
    "type": "object",
    "properties": {
        "moments": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "start": {"type": "number"},
                    "end": {"type": "number"},
                    "title": {"type": "string"},
                    "hook": {"type": "string"},
                    "reason": {"type": "string"},
                    "suggestions": {"type": "string"},
                    "scores": {
                        "type": "object",
                        "properties": {k: {"type": "integer"} for k in CRITERIA},
                        "required": list(CRITERIA),
                    },
                },
                "required": ["start", "end", "title", "hook", "reason", "suggestions", "scores"],
            },
        }
    },
    "required": ["moments"],
}

SYSTEM_PROMPT = """Eres un editor senior de contenido corto (TikTok, Reels, YouTube Shorts) en español.
Tu trabajo es encontrar en una transcripción los fragmentos con más potencial para funcionar como clip independiente.

Reglas:
- Cada fragmento debe entenderse SOLO, sin haber visto el resto del video.
- Debe empezar con una frase que enganche (pregunta, afirmación fuerte, dato sorprendente, conflicto) y terminar en un cierre natural, nunca a mitad de una idea.
- Duración entre {min_d} y {max_d} segundos. Usa los tiempos [inicio-fin] de la transcripción.
- No elijas saludos, despedidas, patrocinios ni relleno.
- No inventes nada que no esté en la transcripción.
- Califica cada criterio de 0 a 100 siendo exigente (la mayoría de fragmentos normales están entre 40 y 70):
  hook (engancha en 3 segundos), contexto (se entiende solo), emocion (sorpresa, humor, curiosidad),
  valor (enseña o aporta algo), narrativa (cuenta una mini historia), retencion (invita a verlo completo).
- title: título corto y atractivo para el clip (máx. 8 palabras).
- hook: la frase gancho tal como aparece o una versión corta para poner en pantalla.
- reason: por qué funciona, en una frase.
- suggestions: cómo mejorarlo al editar (texto en pantalla, recorte, contexto a añadir, zoom, música).
Responde SOLO con JSON."""


# ---------- utilidades de transcripción ----------

def chunk_segments(segments: list[dict], chunk_sec: float = 480, overlap: float = 45) -> list[list[dict]]:
    if not segments:
        return []
    chunks, start = [], segments[0]["start"]
    end_total = segments[-1]["end"]
    while start < end_total:
        stop = start + chunk_sec
        chunk = [s for s in segments if s["end"] > start and s["start"] < stop]
        if chunk:
            chunks.append(chunk)
        start = stop - overlap
    return chunks


def format_chunk(chunk: list[dict]) -> str:
    return "\n".join(f"[{s['start']:.1f}-{s['end']:.1f}] {s['text']}" for s in chunk)


def text_between(segments: list[dict], start: float, end: float) -> str:
    words = []
    for s in segments:
        if s["end"] < start or s["start"] > end:
            continue
        if s.get("words"):
            words += [w["word"] for w in s["words"] if w["end"] > start + 0.05 and w["start"] < end - 0.05]
        else:
            words.append(s["text"])
    return " ".join(words).strip()


def snap(start: float, end: float, segments: list[dict], min_d: float, max_d: float,
         duration: float, scenes: list[float] | None = None) -> tuple[float, float] | None:
    """Ajusta un rango a frases completas respetando la duración mínima/máxima."""
    if not segments or end <= start:
        return None
    i = min(range(len(segments)), key=lambda k: abs(segments[k]["start"] - start))
    j = min(range(len(segments)), key=lambda k: abs(segments[k]["end"] - end))
    if j < i:
        j = i
    # Extender si es demasiado corto
    while segments[j]["end"] - segments[i]["start"] < min_d and j + 1 < len(segments):
        if segments[j + 1]["end"] - segments[i]["start"] > max_d:
            break
        j += 1
    # Recortar si es demasiado largo
    while segments[j]["end"] - segments[i]["start"] > max_d and j > i:
        j -= 1
    s, e = segments[i]["start"], segments[j]["end"]
    if e - s > max_d:  # un solo segmento enorme: cortar en la última palabra que quepa
        words = segments[i].get("words") or []
        fit = [w for w in words if w["end"] <= s + max_d]
        e = fit[-1]["end"] if fit else s + max_d
    if e - s < min_d * 0.6:
        return None
    s = max(0.0, s - 0.15)
    e = min(duration, e + 0.35) if duration else e + 0.35
    if scenes:
        near = [c for c in scenes if s < c < s + 0.8]
        if near:
            s = near[0]
    return round(s, 2), round(e, 2)


def energy_score(start: float, end: float, energy: list[float]) -> float:
    if not energy:
        return 50.0
    a, b = int(start / ENERGY_HOP), max(int(end / ENERGY_HOP), int(start / ENERGY_HOP) + 1)
    window = energy[a:b]
    return round(100 * sum(window) / len(window), 1) if window else 50.0


def weighted(scores: dict, weights: dict | None = None) -> float:
    w = weights or CRITERIA
    return sum(w.get(k, CRITERIA[k]) * float(scores.get(k, 0)) for k in CRITERIA)


def learned_weights() -> dict | None:
    """Pesos aprendidos de lo que apruebas/descartas (si ya hay datos suficientes)."""
    try:
        from .. import taste
        p = taste.profile()
        return p["criteria"] if p.get("ready") else None
    except Exception:  # noqa: BLE001
        return None


def dedupe(cands: list[dict], max_overlap: float = 0.3) -> list[dict]:
    kept: list[dict] = []
    for c in sorted(cands, key=lambda x: x["score"], reverse=True):
        ok = True
        for k in kept:
            inter = max(0.0, min(c["end"], k["end"]) - max(c["start"], k["start"]))
            shorter = min(c["end"] - c["start"], k["end"] - k["start"])
            if shorter > 0 and inter / shorter > max_overlap:
                ok = False
                break
        if ok:
            kept.append(c)
    return kept


# ---------- métodos de selección ----------

def llm_moments(segments, duration, energy, scenes, cfg, llm_cfg,
                progress: Callable[[float, str], None]) -> list[dict]:
    chunks = chunk_segments(segments)
    per_chunk = max(2, min(6, round(cfg["max_clips"] * 1.8 / max(len(chunks), 1)) + 1))
    system = SYSTEM_PROMPT.format(min_d=int(cfg["min_dur"]), max_d=int(cfg["max_dur"]))
    if llm_cfg.get("context"):
        system += "\n\n" + llm_cfg["context"]
    cands = []
    weights = learned_weights()
    for n, chunk in enumerate(chunks):
        progress(n / len(chunks), f"IA analizando bloque {n + 1} de {len(chunks)}")
        user = (f"Encuentra hasta {per_chunk} fragmentos con potencial viral en este bloque.\n\n"
                f"TRANSCRIPCIÓN:\n{format_chunk(chunk)}")
        data = llm.chat_json(llm_cfg["url"], llm_cfg["model"], system, user, MOMENT_SCHEMA)
        for m in data.get("moments", []):
            try:
                rng = snap(float(m["start"]), float(m["end"]), segments,
                           cfg["min_dur"], cfg["max_dur"], duration, scenes)
            except (KeyError, TypeError, ValueError):
                continue
            if not rng:
                continue
            scores = {k: max(0, min(100, int(m.get("scores", {}).get(k, 0)))) for k in CRITERIA}
            en = energy_score(*rng, energy)
            cands.append({
                "start": rng[0], "end": rng[1],
                "title": str(m.get("title", "")).strip()[:120] or "Momento destacado",
                "hook": str(m.get("hook", "")).strip()[:300],
                "reason": str(m.get("reason", "")).strip()[:500],
                "suggestions": str(m.get("suggestions", "")).strip()[:800],
                "scores": {**scores, "energia": en},
                "score": round(weighted(scores, weights) * 0.9 + en * 0.1, 1),
                "source": "ai",
            })
    progress(1.0, "Seleccionando los mejores")
    return cands


HOOK_WORDS = re.compile(
    r"\b(nadie|nunca|secreto|verdad|error|increíble|imagina|por qué|porque nadie|dinero|"
    r"cuidado|importante|historia|aprendí|descubrí|lo peor|lo mejor|la clave|mentira|"
    r"sabías|truco|problema|millones|miedo)\b", re.I)


def heuristic_moments(segments, duration, energy, scenes, cfg) -> list[dict]:
    target = (cfg["min_dur"] + cfg["max_dur"]) / 2
    cands = []
    for i in range(len(segments)):
        rng = snap(segments[i]["start"], segments[i]["start"] + target, segments,
                   cfg["min_dur"], cfg["max_dur"], duration, scenes)
        if not rng:
            continue
        text = text_between(segments, *rng)
        first = segments[i]["text"]
        wps = len(text.split()) / max(rng[1] - rng[0], 1)
        en = energy_score(*rng, energy)
        ritmo = max(0.0, min(100.0, (wps - 1.5) / 2.0 * 100))
        senales = min(100, 25 * len(HOOK_WORDS.findall(text)) + 30 * first.count("?")
                      + 15 * text.count("!") + (20 if HOOK_WORDS.search(first) else 0))
        score = round(0.45 * en + 0.25 * ritmo + 0.30 * senales, 1)
        cands.append({
            "start": rng[0], "end": rng[1],
            "title": (first[:60] + "…") if len(first) > 60 else first,
            "hook": first[:200], "reason": "Selección heurística (sin IA): energía, ritmo y señales de texto.",
            "suggestions": "",
            "scores": {"energia": en, "ritmo": round(ritmo, 1), "senales": senales},
            "score": score, "source": "heuristic",
        })
    return cands


def energy_moments(segments, duration, energy, scenes, cfg) -> list[dict]:
    """Momentos de emoción del juego/stream: picos sostenidos de volumen (gritos, explosiones, risas)."""
    import numpy as np
    if len(energy) < 20:
        return []
    e = np.array(energy, dtype=float)
    k = max(1, int(3.0 / ENERGY_HOP))
    smooth = np.convolve(e, np.ones(k) / k, mode="same")
    p50, p90, p99 = np.percentile(smooth, [50, 90, 99])
    target = (cfg["min_dur"] + cfg["max_dur"]) / 2
    gap = int(cfg["max_dur"] / ENERGY_HOP)
    order = np.argsort(-smooth)
    taken: list[int] = []
    cands = []
    for i in order:
        if smooth[i] < p90 or len(cands) >= cfg["max_clips"] * 3:
            break
        if any(abs(i - j) < gap for j in taken):
            continue
        taken.append(int(i))
        t = i * ENERGY_HOP
        a, b = max(0.0, t - target * 0.6), min(duration, t + target * 0.4)
        rng = snap(a, b, segments, cfg["min_dur"], cfg["max_dur"], duration, scenes) if segments else None
        if not rng:
            rng = (round(a, 2), round(b, 2))
        score = float(np.clip(55 + 45 * (smooth[i] - p50) / max(p99 - p50, 1e-3), 0, 100))
        text = text_between(segments, *rng) if segments else ""
        mm, ss = divmod(int(t), 60)
        cands.append({
            "start": rng[0], "end": rng[1],
            "title": (" ".join(text.split()[:8]) or f"Momento intenso {mm}:{ss:02d}")[:120],
            "hook": " ".join(text.split()[:14]), "reason": "Pico de emoción en el audio (gritos, acción, risas).",
            "suggestions": "Ideal con la cámara arriba y el juego abajo; añade un zoom o texto en el clímax.",
            "scores": {"energia": round(energy_score(*rng, energy), 1), "pico": round(score, 1)},
            "score": round(score, 1), "source": "energy",
        })
    return cands


def find_highlights(segments, duration, energy, scenes, cfg, llm_cfg,
                    progress: Callable[[float, str], None] = lambda p, s: None) -> tuple[list[dict], str]:
    stream = cfg.get("mode") == "stream"
    if not segments and not stream:
        return [], "none"
    method = "heuristic"
    cands: list[dict] = []
    st = llm.status(llm_cfg["url"])
    if segments and st["available"] and llm_cfg["model"] in st["models"]:
        cands = llm_moments(segments, duration, energy, scenes, cfg, llm_cfg,
                            (lambda p, s: progress(0.85 * p, s)) if llm_cfg.get("judge") is not None else progress)
        method = "ai"
    if stream:  # en streams también cuentan los momentos de emoción aunque no hables
        cands += energy_moments(segments, duration, energy, scenes, cfg)
        method = "stream" if method != "ai" else "ai+stream"
    if not cands and segments:
        cands = heuristic_moments(segments, duration, energy, scenes, cfg)
        method = "heuristic"
    pool = dedupe(cands)
    jcfg = llm_cfg.get("judge")
    if jcfg is not None and pool:  # segunda opinión: revisa 50 % más candidatos y se queda con los mejores
        from . import judge
        pool = judge.judge_all(pool[: int(cfg["max_clips"] * 1.5 + 0.5)], segments, energy, cfg,
                               jcfg if jcfg.get("model") else None, duration,
                               lambda p, s: progress(0.85 + 0.15 * p, s))
        pool = dedupe(pool)  # los recortes pueden cambiar solapamientos
    best = pool[: cfg["max_clips"]]
    best.sort(key=lambda c: c["score"], reverse=True)
    return best, method
