"""Qué recurso visual complementa cada momento del video (lo decide la IA local, anclado a lo que se dice).

Dos modos:
- "cards": tarjetas sueltas encima del video (pocas por minuto).
- "background": b-roll continuo detrás de la cámara (estilo Baitybait): tramos seguidos de 3–7 s que cubren
  todo lo que se pueda; huecos solo donde no hay nada concreto que mostrar.
Nada se inventa: cada recurso debe corresponder a algo que se dice en ese momento (se valida con la transcripción).
"""
from __future__ import annotations

import json

from .. import db
from ..ai import llm
from . import store

WINDOW_S = 90.0

PLAN_SCHEMA = {
    "type": "object",
    "properties": {"items": {"type": "array", "items": {"type": "object", "properties": {
        "t0": {"type": "number"}, "t1": {"type": "number"}, "entity": {"type": "string"},
        "what": {"type": "string", "enum": list(store.WHATS)}, "query": {"type": "string"},
        "label": {"type": "string"}, "priority": {"type": "integer"}},
        "required": ["t0", "t1", "entity", "what"]}}},
    "required": ["items"],
}

SYSTEM = """Eres el editor de b-roll de un canal de YouTube en español. Lees un tramo de la transcripción (con tiempos en
segundos) y eliges qué mostrar en pantalla para complementar lo que se dice EN ESE MOMENTO. Responde solo JSON.
Cada elemento:
- t0, t1: cuándo se muestra (dentro del tramo, alineado a cuando se dice la cosa).
- entity: la cosa concreta que se menciona tal cual (marca, producto, juego, sitio web, persona, lugar, evento).
- what: "logo" (marca/empresa/tienda), "producto" (consola, tarjeta, objeto), "captura_web" (la página o sitio web
  del que se habla), "noticia" (titular o nota sobre un hecho), "persona", "lugar", "clip" (trailer o video corto
  oficial), "juego" (gameplay del juego mencionado), "texto_clave" (una frase corta y fuerte que se dice, sin buscar
  nada: entity = esa frase, máximo 5 palabras, tal cual se dijo).
- query: búsqueda web que encuentre la mejor imagen o video REAL de eso (en español o inglés, lo que funcione mejor;
  vacío para texto_clave).
- label: rótulo opcional de máximo 5 palabras, solo con datos que se dicen.
- priority: 3 = clave para entender, 2 = útil, 1 = de relleno.
Reglas: solo cosas que realmente se dicen en el tramo (nada inventado ni supuesto); prefiere sustantivos concretos;
nada de memes, caricaturas ni escenas de películas o series; no repitas la misma cosa seguida si hay otra opción."""


def _lines(words: list[dict], a: float, b: float, step: float = 5.0) -> str:
    out, cur, t0 = [], [], None
    for w in words:
        if w["start"] < a or w["start"] >= b:
            continue
        if t0 is None:
            t0 = w["start"]
        cur.append(str(w.get("word", "")).strip())
        if w["start"] - t0 >= step or str(w.get("word", "")).rstrip().endswith((".", "?", "!")):
            out.append(f"[{t0:.1f}] {' '.join(cur)}")
            cur, t0 = [], None
    if cur:
        out.append(f"[{t0:.1f}] {' '.join(cur)}")
    return "\n".join(out)


def _said(words: list[dict], a: float, b: float) -> str:
    return " ".join(str(w.get("word", "")).strip() for w in words if a <= w["start"] <= b)


def _align(words: list[dict], entity: str, a: float, b: float) -> float | None:
    """Inicio de la primera palabra (dentro de [a, b]) que corresponde al token principal de la entidad."""
    et = store.tokens(entity)
    if not et:
        return None
    main = max(et, key=len)
    for w in words:
        if a <= w["start"] <= b and store._tok_in(main, set(store.norm(w.get("word", "")).split())):
            return float(w["start"])
    return None


def _context(understood: dict | None, facts: dict | None) -> str:
    parts = []
    if understood:
        if understood.get("tema"):
            parts.append(f"Tema del video: {understood['tema']}")
        if understood.get("no_confundir"):
            parts.append("No confundir: " + "; ".join(map(str, understood["no_confundir"]))[:400])
    if facts:
        if facts.get("terminos_actuales"):
            parts.append("Nombres actuales: " + ", ".join(map(str, facts["terminos_actuales"]))[:300])
        if facts.get("contexto"):
            parts.append("Contexto verificado: " + " | ".join(map(str, facts["contexto"]))[:500])
    return "\n".join(parts)


def _clean(items: list[dict], words: list[dict], a: float, b: float, mode: str) -> list[dict]:
    out = []
    lo, hi = (3.0, 7.0) if mode == "background" else (2.5, 7.0)
    for it in items:
        what = it.get("what")
        ent = str(it.get("entity") or "").strip()[:80]
        if what not in store.WHATS or not ent:
            continue
        try:
            t0, t1 = float(it["t0"]), float(it["t1"])
        except (KeyError, TypeError, ValueError):
            continue
        ln = max(lo, min(hi, t1 - t0))
        t0 = max(a, min(t0, b))
        at = _align(words, ent, t0 - 4, min(b, t0 + ln + 4))
        if at is not None:
            t0 = max(a, at - 0.15)
        t1 = t0 + ln
        said = _said(words, t0 - 1.5, t1 + 1.5)
        if not store.mentions(ent, said):
            continue  # no se dice: fuera (no se inventa nada)
        if what == "texto_clave":
            ent = " ".join(ent.split()[:5])
        label = " ".join(str(it.get("label") or "").split()[:5])
        said_words = set(store.norm(_said(words, t0 - 4, t1 + 2)).split())  # el rótulo puede resumir lo de alrededor
        if label and not all(store._tok_in(t, said_words) for t in store.tokens(label)):
            label = ""  # el rótulo solo puede llevar lo que se dice
        q = str(it.get("query") or "").strip()[:120] if what != "texto_clave" else ""
        if what != "texto_clave" and not q:
            q = ent
        out.append({"t0": round(t0, 2), "t1": round(t1, 2), "entity": ent, "what": what, "query": q,
                    "said": said[:240], "label": label, "priority": max(1, min(3, int(it.get("priority") or 2)))})
    return out


def _dedupe(needs: list[dict], min_gap: float) -> list[dict]:
    """Sin solapes: se queda primero lo de más prioridad."""
    keep: list[dict] = []
    for n in sorted(needs, key=lambda x: (-x["priority"], x["t0"])):
        if all(n["t1"] + min_gap <= k["t0"] or n["t0"] >= k["t1"] + min_gap for k in keep):
            keep.append(n)
    return sorted(keep, key=lambda x: x["t0"])


def _fill_gaps(needs: list[dict], max_gap: float = 2.0, max_len: float = 7.0) -> None:
    """Modo fondo: estira tramos para tapar huecos cortos (sin pasar de 7 s)."""
    for a, b in zip(needs, needs[1:]):
        gap = b["t0"] - a["t1"]
        if 0 < gap <= max_gap:
            room = max(0.0, max_len - (a["t1"] - a["t0"]))
            take = min(gap, room)
            a["t1"] = round(a["t1"] + take, 2)
            if a["t1"] < b["t0"]:
                b["t0"] = round(a["t1"], 2)


def plan_needs(tl: dict, understood: dict | None, research_facts: dict | None, per_min: float,
               max_items: int = 40, progress=None, mode: str = "cards") -> list[dict]:
    words = [w for w in tl.get("words") or [] if "start" in w]
    if not words or per_min <= 0:
        return []
    url, model = db.get_setting("ollama_url"), db.role_model("director")
    try:
        st = llm.status(url)
        if not st["available"] or (model and model not in st["models"]):
            return []
    except Exception:  # noqa: BLE001
        return []
    dur = float(tl.get("duration") or words[-1].get("end", words[-1]["start"]))
    ctx = _context(understood, research_facts)
    wins = []
    t = 0.0
    while t < dur:
        wins.append((t, min(dur, t + WINDOW_S)))
        t += WINDOW_S
    needs: list[dict] = []
    for i, (a, b) in enumerate(wins):
        if progress:
            progress(i / len(wins), f"Pensando qué mostrar ({i + 1}/{len(wins)})")
        text = _lines(words, a, b)
        if not text.strip():
            continue
        n = max(1, round(per_min * (b - a) / 60))
        if mode == "background":
            ask = (f"Modo FONDO: lo que elijas ocupará toda la pantalla detrás de la cámara. Cubre el tramo de {a:.0f} s a "
                   f"{b:.0f} s con unos {n} elementos SEGUIDOS de 3 a 7 s cada uno (sin huecos salvo donde no se diga "
                   "nada concreto). Usa texto_clave para frases fuertes cuando no haya nada que buscar.")
        else:
            ask = (f"Modo TARJETAS: elige unos {n} momentos (máximo) entre {a:.0f} s y {b:.0f} s, de 2.5 a 7 s cada uno, "
                   "separados entre sí; solo lo que de verdad aporta.")
        user = f"{ctx}\n\n{ask}\n\nTRANSCRIPCIÓN:\n{text}"
        try:
            out = llm.chat_json(url, model, SYSTEM, user, PLAN_SCHEMA, timeout=600, num_ctx=8192, max_tokens=1600)
        except Exception:  # noqa: BLE001 — sin IA no hay plan (no se inventa)
            continue
        items = out.get("items") if isinstance(out, dict) else None
        needs += _clean(items or [], words, a, b, mode)
    gap = 0.0 if mode == "background" else max(2.0, 60.0 / max(per_min, 0.1) * 0.4)
    needs = _dedupe(needs, gap)
    if mode == "background":
        _fill_gaps(needs)
    if len(needs) > max_items:  # se quedan los más importantes, en orden de tiempo
        needs = sorted(sorted(needs, key=lambda x: (-x["priority"], x["t0"]))[:max_items], key=lambda x: x["t0"])
    if progress:
        progress(1.0, f"{len(needs)} recursos por buscar")
    return needs


def as_json(needs: list[dict]) -> str:
    return json.dumps(needs, ensure_ascii=False)
