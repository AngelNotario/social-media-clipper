"""Duración final: deja el video en el tiempo que pides quedándote con lo mejor.

Después de quitar silencios y muletillas, si el video sigue más largo que la duración elegida en el brief:
1. Se agrupan las tomas en BLOQUES de 20–45 s que terminan donde termina una frase (no corta a media idea).
2. Cada bloque recibe una calificación: la IA lee lo que dices y decide qué tan importante es (gancho, dato clave,
   momento gracioso o clímax = alto; esperas, «está cargando», repeticiones = bajo). Se combina con la energía de
   tu voz y qué tan seguido hablas. Sin IA disponible, se usa solo energía y ritmo.
3. Se quedan los bloques mejor calificados hasta llegar a la duración, en su orden original. El primero (gancho)
   tiene preferencia. Nunca alarga: si el video ya dura menos, no cambia nada.
"""
from __future__ import annotations

import statistics

from ..ai import audio, llm

MIN_BLOCK, MAX_BLOCK = 20.0, 45.0


def _blocks(segs: list[tuple[float, float]], words: list[dict]) -> list[dict]:
    out, cur = [], None
    for i, (a, b) in enumerate(segs):
        if cur is None:
            cur = {"i0": i, "i1": i, "len": 0.0, "start": a}
        cur["i1"], cur["len"], cur["end"] = i, cur["len"] + (b - a), b
        last = next((w["word"] for w in reversed(words) if a <= w["start"] < b), "")
        ends_sentence = last.rstrip().endswith((".", "?", "!", "…"))
        gap_next = (segs[i + 1][0] - b) if i + 1 < len(segs) else 99
        if cur["len"] >= MAX_BLOCK or (cur["len"] >= MIN_BLOCK and (ends_sentence or gap_next > 1.5)):
            out.append(cur)
            cur = None
    if cur:
        if out and cur["len"] < MIN_BLOCK / 2:  # un pedacito final se une al bloque anterior
            out[-1].update(i1=cur["i1"], end=cur["end"], len=out[-1]["len"] + cur["len"])
        else:
            out.append(cur)
    for k, bl in enumerate(out):
        bl["id"] = k
        bl["text"] = " ".join(w["word"] for w in words
                              if any(a <= w["start"] < b for a, b in segs[bl["i0"]:bl["i1"] + 1]))
    return out


def _heuristic(blocks: list[dict], segs: list[tuple[float, float]], energy: list[float]) -> None:
    hop = audio.ENERGY_HOP
    dens, ener = [], []
    for bl in blocks:
        dens.append(len(bl["text"].split()) / max(bl["len"], 1.0))
        vals = [energy[j] for a, b in segs[bl["i0"]:bl["i1"] + 1]
                for j in range(int(a / hop), min(len(energy), int(b / hop) + 1))] if energy else []
        ener.append(statistics.fmean(vals) if vals else 0.0)

    def norm(xs):
        lo, hi = min(xs), max(xs)
        return [(x - lo) / (hi - lo) if hi > lo else 0.5 for x in xs]

    for bl, d, e in zip(blocks, norm(dens), norm(ener)):
        bl["heur"] = round(1 + 9 * (0.5 * d + 0.5 * e), 2)


SCHEMA = {"type": "object", "properties": {"scores": {"type": "array", "items": {
    "type": "object", "properties": {"id": {"type": "integer"}, "score": {"type": "integer"}},
    "required": ["id", "score"]}}}, "required": ["scores"]}


def _llm_scores(blocks: list[dict], cfg: dict, target_s: float, video_type: str, progress) -> dict[int, int]:
    system = ("Eres editor de video. Te doy bloques numerados de la transcripción de un video. Califica cada bloque "
              "de 1 a 10 según qué tanto merece quedarse en la versión final de "
              f"{round(target_s / 60, 1)} minutos ({'video corto' if video_type == 'short' else 'video de YouTube'}). "
              "10 = imprescindible: gancho, dato clave, conclusión, momento gracioso o clímax. "
              "1 = relleno: esperas, «está cargando», problemas técnicos sin interés, repeticiones, divagaciones. "
              "Responde solo JSON con una calificación por cada id.")
    scores: dict[int, int] = {}
    batch = 40
    for k in range(0, len(blocks), batch):
        part = blocks[k:k + batch]
        progress(k / max(len(blocks), 1), f"Eligiendo las mejores partes ({k + len(part)}/{len(blocks)} bloques)")
        user = "\n".join(f"[{bl['id']}] ({round(bl['len'])} s) {bl['text'][:360]}" for bl in part)
        try:
            d = llm.chat_json(cfg["url"], cfg["model"], system, user, SCHEMA, num_ctx=12288, max_tokens=900,
                              timeout=600)
        except Exception:  # noqa: BLE001 — sin IA para este lote, queda la calificación por energía
            continue
        for s in d.get("scores") or []:
            try:
                scores[int(s["id"])] = max(1, min(10, int(s["score"])))
            except (KeyError, TypeError, ValueError):
                continue
    return scores


def select(segs: list[tuple[float, float]], words: list[dict], energy: list[float], target_s: float,
           cfg: dict | None = None, video_type: str = "long",
           progress=lambda p, s: None) -> tuple[list[tuple[float, float]], dict]:
    """Tomas que se quedan para llegar a `target_s` (± 5 %), en orden. Devuelve (tomas, reporte)."""
    total = sum(b - a for a, b in segs)
    info = {"target": round(target_s, 1), "before": round(total, 1), "after": round(total, 1), "removed_blocks": 0}
    if not segs or target_s <= 0 or total <= target_s * 1.03:
        info["note"] = "el video ya dura eso o menos: no se recortó"
        return segs, info
    blocks = _blocks(segs, words)
    _heuristic(blocks, segs, energy)
    llm_s = _llm_scores(blocks, cfg, target_s, video_type, progress) if cfg else {}
    for bl in blocks:
        s = bl["heur"]
        if bl["id"] in llm_s:
            s = 0.75 * llm_s[bl["id"]] + 0.25 * bl["heur"]
        if bl["id"] == 0:
            s += 2.0  # el gancho del inicio
        elif bl["id"] == len(blocks) - 1:
            s += 1.0  # el cierre
        bl["score"] = round(s, 2)
    keep, acc = set(), 0.0
    for bl in sorted(blocks, key=lambda x: -x["score"]):
        if acc + bl["len"] <= target_s * 1.05:
            keep.add(bl["id"])
            acc += bl["len"]
        if acc >= target_s * 0.97:
            break
    if not keep:  # un solo bloque ya es más largo que la duración pedida: el mejor, recortado
        best = max(blocks, key=lambda x: x["score"])
        keep.add(best["id"])
    out = [segs[i] for bl in blocks if bl["id"] in keep for i in range(bl["i0"], bl["i1"] + 1)]
    if sum(b - a for a, b in out) > target_s * 1.05:  # recorta el final del último bloque al tiempo exacto
        trimmed, acc2 = [], 0.0
        for a, b in out:
            if acc2 + (b - a) > target_s:
                if target_s - acc2 >= 1.0:
                    trimmed.append((a, a + (target_s - acc2)))
                break
            trimmed.append((a, b))
            acc2 += b - a
        out = trimmed
    info.update(after=round(sum(b - a for a, b in out), 1), blocks=len(blocks), removed_blocks=len(blocks) - len(keep),
                method="ia" if llm_s else "energía y ritmo de voz",
                kept=[{"desde": round(bl["start"], 1), "hasta": round(bl["end"], 1), "nota": bl["score"]}
                      for bl in blocks if bl["id"] in keep][:60])
    return out, info
