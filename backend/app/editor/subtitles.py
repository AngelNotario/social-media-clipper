"""Subtítulos estilo 'shorts' en formato ASS (quemados con FFmpeg)."""
from __future__ import annotations

import re

STYLES = {
    # Palabra activa resaltada en amarillo, 1-3 palabras por pantalla
    "karaoke": {"font": "Arial Black", "size": 84, "max_words": 3, "highlight": True},
    # Frases cortas sin resaltado
    "simple": {"font": "Arial", "size": 70, "max_words": 6, "highlight": False},
}

POSITIONS = {"bottom": 560, "middle": 860, "top": 1350}  # MarginV desde abajo en un lienzo de 1920


def _ts(t: float) -> str:
    t = max(0.0, t)
    h, rem = divmod(t, 3600)
    m, s = divmod(rem, 60)
    return f"{int(h)}:{int(m):02d}:{s:05.2f}"


def _clean(text: str) -> str:
    return text.replace("\\", "").replace("{", "(").replace("}", ")").replace("\n", " ")


def clip_words(segments: list[dict], start: float, end: float) -> list[dict]:
    out = []
    for s in segments:
        if s["end"] < start or s["start"] > end:
            continue
        for w in s.get("words") or []:
            if w["end"] > start + 0.02 and w["start"] < end - 0.02:
                out.append({"start": max(w["start"], start) - start,
                            "end": min(w["end"], end) - start, "word": w["word"]})
    return out


# palabras que no deben quedar colgando al final de un subtítulo ("AMIGOS EL" → "AMIGOS" / "EL CONTEXTO")
CONNECTORS = {"el", "la", "los", "las", "un", "una", "unos", "unas", "de", "del", "a", "al", "y", "e", "o", "u",
              "que", "en", "con", "por", "para", "mi", "mis", "tu", "tus", "su", "sus", "lo", "le", "les", "se",
              "no", "ni", "pero", "como", "muy", "más", "es", "the", "a", "an", "of", "to", "and", "in"}


def _bare(w: str) -> str:
    return re.sub(r"[^\wáéíóúñü]", "", w.lower())


def group_words(words: list[dict], max_words: int) -> list[list[dict]]:
    """Agrupa como lo haría un editor: corta en fin de frase, en pausas y en comas (si el grupo no queda de una
    palabra), y nunca deja un artículo/preposición colgando al final: lo pasa al grupo siguiente."""
    groups, cur = [], []
    for i, w in enumerate(words):
        cur.append(w)
        gap = words[i + 1]["start"] - w["end"] if i + 1 < len(words) else 99
        end_sentence = re.search(r"[.!?…]$", w["word"])
        comma = re.search(r"[,;:]$", w["word"]) and len(cur) >= 2
        if len(cur) >= max_words or end_sentence or comma or gap > 0.6:
            groups.append(cur)
            cur = []
    if cur:
        groups.append(cur)
    out: list[list[dict]] = []
    carry: list[dict] = []
    for gi, g in enumerate(groups):
        g = carry + g
        carry = []
        nxt = groups[gi + 1] if gi + 1 < len(groups) else None
        while (nxt is not None and len(g) > 1 and _bare(g[-1]["word"]) in CONNECTORS
               and not re.search(r"[.!?…,;:]$", g[-1]["word"]) and nxt[0]["start"] - g[-1]["end"] < 0.6):
            carry.insert(0, g.pop())
        out.append(g)
    if carry:
        out.append(carry)
    return out


def build_ass(words: list[dict], duration: float, style: str = "karaoke", position: str = "bottom",
              title: str | None = None, title_secs: float = 3.5, width: int = 1080, height: int = 1920) -> str:
    st = STYLES.get(style, STYLES["karaoke"])
    margin_v = POSITIONS.get(position, POSITIONS["bottom"])
    lines = [
        "[Script Info]", "ScriptType: v4.00+", f"PlayResX: {width}", f"PlayResY: {height}",
        "WrapStyle: 0", "ScaledBorderAndShadow: yes", "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
        "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding",
        f"Style: Sub,{st['font']},{st['size']},&H00FFFFFF,&H00FFFFFF,&H00000000,&H96000000,"
        f"-1,0,0,0,100,100,0,0,1,6,3,2,80,80,{margin_v},1",
        "Style: Title,Arial Black,62,&H00FFFFFF,&H00FFFFFF,&H28000000,&H28000000,"
        "-1,0,0,0,100,100,0,0,3,18,0,8,90,90,230,1",
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    if title:
        lines.append(f"Dialogue: 1,{_ts(0)},{_ts(min(title_secs, duration))},Title,,0,0,0,,"
                     f"{{\\fad(150,250)}}{_clean(title.upper())}")

    for g in group_words(words, st["max_words"]):
        g_start, g_end = g[0]["start"], g[-1]["end"]
        upper = [_clean(w["word"].upper()) for w in g]
        if not st["highlight"]:
            lines.append(f"Dialogue: 0,{_ts(g_start)},{_ts(g_end)},Sub,,0,0,0,,{' '.join(upper)}")
            continue
        for i, w in enumerate(g):
            a = w["start"] if i else g_start
            b = g[i + 1]["start"] if i + 1 < len(g) else g_end
            if b <= a:
                continue
            parts = [
                f"{{\\c&H00E5FF&\\fscx112\\fscy112}}{t}{{\\c&HFFFFFF&\\fscx100\\fscy100}}" if k == i else t
                for k, t in enumerate(upper)
            ]
            lines.append(f"Dialogue: 0,{_ts(a)},{_ts(b)},Sub,,0,0,0,,{' '.join(parts)}")
    return "\n".join(lines) + "\n"
