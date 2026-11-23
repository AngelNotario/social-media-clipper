"""Diccionario de nombres: palabras que Whisper debe escribir bien (marcas, juegos, tiendas, apodos).

- `vocabulary` (Configuración): lista de términos, p. ej. «Eneba, GTA 6, Xbox Series S». Se le pasan a Whisper
  como pista (hotwords) y sirven para corregir transcripciones que ya existen sin volver a transcribir.
- `vocab_fixes`: correcciones aprendidas de lo que arreglas a mano en los subtítulos («Neva» → «Eneba»).
  La próxima vez que Whisper se equivoque igual, se corrige solo, en todo el proceso (tema, búsquedas, textos).
"""
from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher

from .. import db

_PUNCT = re.compile(r"^([^\wáéíóúñü]*)(.*?)([^\wáéíóúñü]*)$", re.IGNORECASE)
# palabras comunes que nunca se reemplazan por parecido (evita «nueva» → «Eneba»)
_COMMON = {"nueva", "nuevo", "nueve", "neta", "nena", "nave", "nieve", "vena", "llevaba", "lleva", "eneva"}


def _key(s: str) -> str:
    s = unicodedata.normalize("NFD", s.lower())
    return re.sub(r"[^a-z0-9ñ]", "", "".join(c for c in s if unicodedata.category(c) != "Mn"))


def _sound(k: str) -> str:
    """Clave fonética en español: v=b, z/ce/ci=s, ll=y, sin h y sin vocal inicial (Whisper suele comérsela:
    «Eneba» → «Neva»)."""
    k = k.replace("v", "b").replace("z", "s").replace("ll", "y").replace("h", "").replace("qu", "k").replace("c", "k")
    return k[1:] if len(k) > 4 and k[0] in "aeiou" else k


def terms() -> list[str]:
    raw = db.get_setting("vocabulary") or []
    if isinstance(raw, str):
        raw = re.split(r"[,\n;]+", raw)
    out, seen = [], set()
    for t in raw:
        t = str(t).strip()[:60]
        if t and _key(t) not in seen:
            seen.add(_key(t))
            out.append(t)
    return out[:200]


def fixes() -> dict[str, str]:
    raw = db.get_setting("vocab_fixes") or {}
    return {_key(k): str(v) for k, v in raw.items() if _key(k) and str(v).strip()} if isinstance(raw, dict) else {}


def hotwords() -> str | None:
    """Pista para Whisper (hotwords). None si no hay diccionario."""
    t = terms()
    return " ".join(t)[:600] or None


def _single_terms() -> dict[str, str]:
    return {_key(t): t for t in terms() if " " not in t.strip() and len(_key(t)) >= 4}


def fix_word(word: str, fx: dict[str, str], singles: dict[str, str]) -> str:
    m = _PUNCT.match(word)
    pre, core, post = (m.group(1), m.group(2), m.group(3)) if m else ("", word, "")
    k = _key(core)
    if not k:
        return word
    if k in fx:
        return pre + fx[k] + post
    if k in singles:  # mismo término con otra mayúscula/acento: se escribe como en el diccionario
        return pre + singles[k] + post
    if len(k) >= 4 and k not in _COMMON:
        best, score = None, 0.0
        for tk, t in singles.items():
            if _sound(tk) == _sound(k):
                return pre + t + post
            if abs(len(tk) - len(k)) > 2:
                continue
            r = SequenceMatcher(None, k, tk).ratio()
            if r > score:
                best, score = t, r
        if best and score >= 0.84:
            return pre + best + post
    return word


def apply(words: list[dict]) -> list[dict]:
    """Palabras de Whisper con el diccionario aplicado (copia; no cambia lo guardado)."""
    fx, singles = fixes(), _single_terms()
    if not fx and not singles:
        return words
    out = []
    for w in words:
        nw = fix_word(w["word"], fx, singles)
        out.append(w if nw == w["word"] else {**w, "word": nw})
    return out


def learn_from_fixes(raw_words: list[dict], word_fixes: list[dict]) -> int:
    """Aprende de tus correcciones de subtítulos: si cambiaste una palabra por otra (mismo número de palabras),
    se guarda el par y la palabra corregida entra al diccionario. Devuelve cuántos pares nuevos aprendió."""
    if not word_fixes or not raw_words:
        return 0
    fx = dict(db.get_setting("vocab_fixes") or {})
    vocab = terms()
    keys = {_key(t) for t in vocab}
    learned = 0
    for f in word_fixes:
        try:
            a, b = float(f["src_start"]), float(f["src_end"])
        except (KeyError, TypeError, ValueError):
            continue
        old = [w["word"] for w in raw_words if a - 0.01 <= (w["start"] + w["end"]) / 2 <= b + 0.01]
        new = str(f.get("text") or "").split()
        if not old or len(old) != len(new):
            continue
        for o, n in zip(old, new):
            ko, kn = _key(o), _key(n)
            if not ko or not kn or ko == kn or len(kn) < 3:
                continue
            if SequenceMatcher(None, ko, kn).ratio() < 0.5:  # una palabra distinta, no una corrección
                continue
            clean = _PUNCT.match(n).group(2) or n
            if fx.get(ko) != clean:
                fx[ko] = clean
                learned += 1
            if kn not in keys and clean[:1].isupper():  # nombres propios al diccionario
                vocab.append(clean)
                keys.add(kn)
    if learned:
        db.set_setting("vocab_fixes", dict(list(fx.items())[-500:]))
        db.set_setting("vocabulary", vocab[:200])
    return learned
