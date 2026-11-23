"""Juez de gancho y retención: segunda opinión independiente sobre cada clip candidato.

Un editor profesional no publica lo primero que elige: lo revisa con otros ojos. Aquí hay dos jueces:
1. Reglas medibles (sin IA): cuándo arranca la voz, energía del gancho frente al resto, aire muerto,
   velocidad del habla, si empieza y termina en frase completa y dónde cae el pico (el remate).
2. Un LLM con rúbrica de editor de retención (por defecto un modelo DISTINTO al que eligió los momentos):
   gancho, retención, remate y si se entiende solo; propone recortes de inicio/fin y el texto de gancho
   para los primeros 3 segundos.
La nota final combina selección + reglas + juez, y los recortes propuestos se aplican a frase completa.
"""
from __future__ import annotations

import re
from typing import Callable

from . import llm
from .audio import ENERGY_HOP

HOOK = 3.0
END_PUNCT = re.compile(r"[.!?…]$")

JUDGE_SCHEMA = {
    "type": "object",
    "properties": {
        "hook": {"type": "integer"},
        "retention": {"type": "integer"},
        "payoff": {"type": "integer"},
        "standalone": {"type": "integer"},
        "verdict": {"type": "string", "enum": ["publicar", "mejorar", "descartar"]},
        "reasons": {"type": "array", "items": {"type": "string"}},
        "fix_start": {"type": "number"},
        "fix_end": {"type": "number"},
        "hook_text": {"type": "string"},
        "fix_tip": {"type": "string"},
    },
    "required": ["hook", "retention", "payoff", "standalone", "verdict", "reasons",
                 "fix_start", "fix_end", "hook_text", "fix_tip"],
}

JUDGE_SYSTEM = """Eres un editor de retención para TikTok, Reels y Shorts. Otro editor ya eligió este clip;
tu trabajo es revisarlo con ojo crítico, como quien decide si se publica. Sé exigente: un clip normal saca 5-6.

Califica de 0 a 10:
- hook: ¿los primeros 3 segundos obligan a quedarse? (afirmación fuerte, pregunta, conflicto, acción). Saludos,
  contexto lento, "bueno pues", "oigan" o silencio = 0-3.
- retention: ¿hay tensión o curiosidad que se mantiene hasta el final, sin tramos de relleno?
- payoff: ¿termina con remate, revelación, risa o conclusión? Cortar a mitad de idea = 0-3.
- standalone: ¿lo entiende alguien que no vio el resto del video?

Propón recortes en segundos RELATIVOS al inicio del clip (usa los tiempos [x.x] de la transcripción):
- fix_start: segundo donde debería empezar para que la primera frase sea el gancho (0 = no tocar).
- fix_end: segundo donde debería terminar justo después del remate (0 = no tocar).
Solo recorta si mejora claramente; nunca dejes el clip sin su remate ni sin contexto mínimo.
- hook_text: texto para poner en pantalla los primeros 3 s (máx. 8 palabras, en español, que genere curiosidad
  sin mentir ni inventar; puede ser distinto al título).
- reasons: 2 a 4 razones concretas y cortas (qué funciona y qué falla).
- fix_tip: la mejora de edición más importante en una frase.
- verdict: publicar (listo), mejorar (sirve con ajustes) o descartar.
Responde SOLO con JSON."""


# ---------------- reglas medibles ----------------

def clip_words(segments: list[dict], start: float, end: float) -> list[dict]:
    return [w for s in segments for w in (s.get("words") or [])
            if w.get("start") is not None and start - 0.05 <= w["start"] < end - 0.05]


def _prev_word(segments: list[dict], start: float) -> dict | None:
    prev = [w for s in segments for w in (s.get("words") or []) if w.get("end") is not None and w["end"] <= start + 0.05]
    return prev[-1] if prev else None


def _next_word(segments: list[dict], t: float) -> dict | None:
    nxt = [w for s in segments for w in (s.get("words") or []) if w.get("start") is not None and w["start"] >= t + 0.01]
    return nxt[0] if nxt else None


def features(segments: list[dict], start: float, end: float, energy: list[float]) -> dict:
    words = clip_words(segments, start, end)
    dur = max(end - start, 0.1)
    f: dict = {"duration": round(dur, 2), "words": len(words)}
    if words:
        f["first_word_at"] = round(words[0]["start"] - start, 2)
        f["hook_words"] = sum(1 for w in words if w["start"] - start < HOOK)
        f["wpm"] = round(len(words) / dur * 60)
        gaps = [b["start"] - a["end"] for a, b in zip(words, words[1:]) if b["start"] - a["end"] > 0.8]
        lead = words[0]["start"] - start
        tail = end - words[-1]["end"]
        f["dead_air"] = round((sum(gaps) + max(0, lead - 0.4) + max(0, tail - 0.8)) / dur, 3)
        last = (words[-1].get("word") or "").strip()
        nxt = _next_word(segments, words[-1]["end"])
        seg_end = any(abs((s.get("words") or [{}])[-1].get("end", -1) - words[-1]["end"]) < 1e-3 for s in segments)
        # cierre limpio: puntuación, fin de frase de Whisper o pausa clara después (el habla casual no siempre se puntúa)
        f["ends_clean"] = bool(END_PUNCT.search(last)) or seg_end or nxt is None or nxt["start"] - words[-1]["end"] > 0.6
        pw = _prev_word(segments, start)
        f["starts_clean"] = pw is None or bool(END_PUNCT.search((pw.get("word") or "").strip())) \
            or (words[0]["start"] - (pw.get("end") or 0)) > 0.6
    else:
        f.update(first_word_at=None, hook_words=0, wpm=0, dead_air=1.0, ends_clean=False, starts_clean=True)
    i0, i1 = int(start / ENERGY_HOP), max(int(end / ENERGY_HOP), int(start / ENERGY_HOP) + 1)
    win = energy[i0:i1] if energy else []
    if win:
        mean = sum(win) / len(win) or 1e-6
        k = max(1, int(HOOK / ENERGY_HOP))
        f["hook_energy"] = round((sum(win[:k]) / len(win[:k])) / mean, 2)
        f["peak_at"] = round(win.index(max(win)) / len(win), 2)  # 0 = al inicio, 1 = al final
    else:
        f["hook_energy"], f["peak_at"] = 1.0, 0.5
    return f


def rule_score(f: dict, stream: bool = False) -> tuple[float, list[str]]:
    """0-100 con las razones de lo que resta."""
    score, notes = 100.0, []
    fw = f.get("first_word_at")
    if f["words"] == 0:
        if not stream:
            return 25.0, ["No hay voz en el clip"]
        fw = None
    if fw is not None and fw > 1.2:
        score -= min(30, (fw - 1.2) * 12)
        notes.append(f"La voz empieza a los {fw:.1f} s")
    if f["words"] and f["hook_words"] < 4 and not stream:
        score -= 12
        notes.append("Pocas palabras en los primeros 3 s")
    if f["hook_energy"] < 0.8:
        score -= 12
        notes.append("El inicio es más tranquilo que el resto")
    elif f["hook_energy"] > 1.15:
        score += 4
    if f["dead_air"] > 0.15:
        score -= min(25, (f["dead_air"] - 0.15) * 100)
        notes.append(f"{int(f['dead_air'] * 100)} % de aire muerto")
    if f["words"] and not stream and (f["wpm"] < 110 or f["wpm"] > 230):
        score -= 8
        notes.append(f"Ritmo de habla {'lento' if f['wpm'] < 110 else 'muy rápido'} ({f['wpm']} ppm)")
    if not f["starts_clean"]:
        score -= 10
        notes.append("Empieza a mitad de frase")
    if f["words"] and not f["ends_clean"]:
        score -= 10
        notes.append("Termina a mitad de frase")
    if f["peak_at"] < 0.25 and f["duration"] > 20:
        score -= 6
        notes.append("El momento más intenso llega muy pronto (sin remate al final)")
    if f["duration"] > 60:
        score -= min(10, (f["duration"] - 60) / 3)
    return round(max(0.0, min(100.0, score)), 1), notes


# ---------------- juez LLM ----------------

def _transcript_rel(words: list[dict], start: float) -> str:
    """Transcripción en frases con tiempos relativos al inicio del clip: [3.1] texto…"""
    lines, cur, t0 = [], [], None
    for w in words:
        if t0 is None:
            t0 = w["start"] - start
        cur.append((w.get("word") or "").strip())
        if END_PUNCT.search(cur[-1]) or len(cur) >= 14:
            lines.append(f"[{t0:.1f}] {' '.join(cur)}")
            cur, t0 = [], None
    if cur:
        lines.append(f"[{t0:.1f}] {' '.join(cur)}")
    return "\n".join(lines)


def pick_model(url: str, selection_model: str | None, preferred: str | None = None) -> str | None:
    """Juez distinto del modelo que eligió los momentos cuando hay otro capaz instalado."""
    st = llm.status(url)
    if not st["available"]:
        return None
    models = st["models"]
    if preferred and preferred in models:
        return preferred
    for m in ("gemma4:12b", "qwen3.6:35b-a3b", "qwen3:14b", "gemma3:12b"):
        if m in models and m != selection_model:
            return m
    return selection_model if selection_model in models else (models[0] if models else None)


def llm_judge(url: str, model: str, title: str, words: list[dict], start: float, end: float,
              f: dict, context: str = "") -> dict:
    system = JUDGE_SYSTEM + (("\n\n" + context) if context else "")
    user = (f"Título propuesto: {title}\nDuración: {end - start:.1f} s\n"
            f"Datos medidos: la voz empieza a los {f.get('first_word_at')} s; {f.get('wpm')} palabras/min; "
            f"aire muerto {int(f.get('dead_air', 0) * 100)} %; energía del inicio x{f.get('hook_energy')} "
            f"respecto al resto.\n\nTRANSCRIPCIÓN (tiempos relativos):\n{_transcript_rel(words, start)}")
    d = llm.chat_json(url, model, system, user, JUDGE_SCHEMA, num_ctx=8192, max_tokens=700, keep_alive="5m")
    clamp = lambda v: max(0, min(10, int(v)))  # noqa: E731
    return {
        "hook": clamp(d.get("hook", 0)), "retention": clamp(d.get("retention", 0)),
        "payoff": clamp(d.get("payoff", 0)), "standalone": clamp(d.get("standalone", 0)),
        "verdict": d.get("verdict") if d.get("verdict") in ("publicar", "mejorar", "descartar") else "mejorar",
        "reasons": [str(r).strip()[:160] for r in (d.get("reasons") or []) if str(r).strip()][:4],
        "fix_start": float(d.get("fix_start") or 0), "fix_end": float(d.get("fix_end") or 0),
        # sin emojis ni símbolos: la fuente de los títulos no los dibuja
        "hook_text": " ".join(re.sub(r"[^\w\s¿?¡!.,:;'\"%$#&+-]", "", str(d.get("hook_text", ""))).split()[:10])[:80],
        "fix_tip": str(d.get("fix_tip", "")).strip()[:200],
    }


def llm_score(j: dict) -> float:
    return round((j["hook"] * 0.35 + j["retention"] * 0.25 + j["payoff"] * 0.25 + j["standalone"] * 0.15) * 10, 1)


# ---------------- recortes ----------------

def apply_trims(segments: list[dict], start: float, end: float, j: dict, min_d: float,
                duration: float) -> tuple[float, float, dict]:
    """Aplica los recortes del juez a frontera de palabra. Nunca baja de ~80 % de la duración mínima."""
    words = clip_words(segments, start, end)
    ns, ne, info = start, end, {}
    if words and 0.5 < j.get("fix_start", 0) < (end - start) - min_d * 0.8:
        target = start + j["fix_start"]
        cand = [w for w in words if w["start"] >= target - 0.35]
        if cand and end - (cand[0]["start"] - 0.12) >= min_d * 0.8:
            ns = max(0.0, cand[0]["start"] - 0.12)
    if words and j.get("fix_end", 0) > 0 and (j["fix_end"] - (ns - start)) >= min_d * 0.8 \
            and start + j["fix_end"] < end - 0.8:
        target = start + j["fix_end"]
        cand = [w for w in words if w["end"] <= target + 0.35 and w["start"] > ns]
        if cand:
            ne = min(duration or 1e9, cand[-1]["end"] + 0.35)
    if ns - start > 0.3:
        info["start"] = round(ns - start, 2)
    if end - ne > 0.3:
        info["end"] = round(end - ne, 2)
    if not info:
        return start, end, {}
    return round(ns, 2), round(ne, 2), info


JUDGE_AXES = ("hook", "retention", "payoff", "standalone")


def _profile() -> dict | None:
    try:
        from .. import taste
        return taste.profile()
    except Exception:  # noqa: BLE001
        return None


def judge_clip(c: dict, segments: list[dict], energy: list[float], cfg: dict, jcfg: dict | None,
               duration: float, apply: bool = True) -> dict:
    """Evalúa (y ajusta) un candidato. Devuelve el candidato con 'judge' y la nota final."""
    stream = cfg.get("mode") == "stream"
    f = features(segments, c["start"], c["end"], energy)
    rscore, notes = rule_score(f, stream)
    res = {"rules": rscore, "rule_notes": notes, "features": f, "model": None}
    sel = float(c.get("score") or 0)
    final = sel * 0.6 + rscore * 0.4
    if jcfg and jcfg.get("model") and f["words"] >= 8:
        try:
            words = clip_words(segments, c["start"], c["end"])
            j = llm_judge(jcfg["url"], jcfg["model"], c.get("title", ""), words, c["start"], c["end"], f,
                          jcfg.get("context", ""))
            ns, ne, trim = apply_trims(segments, c["start"], c["end"], j, cfg.get("min_dur", 15), duration)
            if trim and not apply:  # solo sugerir (clip que el usuario ya revisa)
                res["suggest"] = {"start": ns, "end": ne, "cut_start": trim.get("start"), "cut_end": trim.get("end")}
                trim = {}
            if trim:  # reevaluar las reglas con el clip recortado
                c = {**c, "start": ns, "end": ne}
                f2 = features(segments, ns, ne, energy)
                rscore, notes = rule_score(f2, stream)
                res.update(rules=rscore, rule_notes=notes, features=f2)
            res.update(j, model=jcfg["model"], llm=llm_score(j), trimmed=trim)
            final = sel * 0.4 + llm_score(j) * 0.4 + rscore * 0.2
            if j["verdict"] == "descartar":
                final -= 10
        except Exception as exc:  # noqa: BLE001 — el juez no debe tumbar el análisis
            res["error"] = str(exc)[:200]
    # lo aprendido de ti: mezcla de señales, duración preferida y afinidad con tus aprobados
    prof = _profile()
    if prof and prof.get("ready"):
        mix = prof["mix"]
        if "llm" in res:
            final = sel * mix["seleccion"] + res["llm"] * mix["juez"] + rscore * mix["reglas"]
            if res.get("verdict") == "descartar":
                final -= 10
        else:
            final = (sel * mix["seleccion"] + rscore * mix["reglas"]) / ((mix["seleccion"] + mix["reglas"]) or 1)
    dr = prof.get("duration") if prof else None
    cur_dur = c["end"] - c["start"]
    if dr and not (dr[0] <= cur_dur <= dr[1]):
        final -= 6
        res["rule_notes"] = res.get("rule_notes", []) + [f"Fuera de la duración que sueles aprobar ({dr[0]:.0f}-{dr[1]:.0f} s)"]
    if prof and prof.get("model"):
        from .. import taste
        aff = taste.affinity({"features": res["features"], "scores": c.get("scores") or {},
                              "judge": {k: res[k] for k in JUDGE_AXES if k in res}}, prof)
        if aff is not None:
            res["taste"] = aff
            final = final * 0.8 + aff * 0.2
    res["final"] = round(max(0.0, min(100.0, final)), 1)
    scores = {**(c.get("scores") or {}), "seleccion": round(sel, 1), "juez": res.get("llm", rscore),
              "reglas": rscore}
    if "taste" in res:
        scores["gusto"] = res["taste"]
    return {**c, "judge": res, "scores": scores, "score": res["final"]}


def judge_all(cands: list[dict], segments: list[dict], energy: list[float], cfg: dict, jcfg: dict | None,
              duration: float, progress: Callable[[float, str], None] = lambda p, s: None) -> list[dict]:
    out = []
    for n, c in enumerate(cands):
        progress(n / max(len(cands), 1), f"Juez revisando clip {n + 1} de {len(cands)}")
        out.append(judge_clip(c, segments, energy, cfg, jcfg, duration))
    out.sort(key=lambda c: c["score"], reverse=True)
    return out
