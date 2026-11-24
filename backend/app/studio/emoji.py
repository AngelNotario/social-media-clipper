"""Emojis a color junto a los subtítulos (como imagen PNG, no como letra).

FFmpeg/libass en Windows dibuja los emojis en blanco y negro o no los dibuja. Aquí cada emoji se dibuja con
Pillow usando la fuente de emojis a color del sistema (Segoe UI Emoji en Windows, Noto Color Emoji en Linux)
en un PNG con transparencia, y el render lo pone encima del subtítulo mientras se dice la palabra.

Los emojis salen de palabras que se DICEN (diccionario fijo, nada inventado), como mucho uno cada pocos
segundos para que acompañen y no estorben.
"""
from __future__ import annotations

import re
import unicodedata
from pathlib import Path

FONT_CANDIDATES = [
    "C:/Windows/Fonts/seguiemj.ttf",
    "/usr/share/fonts/truetype/noto/NotoColorEmoji.ttf",
    "/usr/share/fonts/noto/NotoColorEmoji.ttf",
    "/System/Library/Fonts/Apple Color Emoji.ttc",
]
MIN_GAP_S = 3.5

# raíz de la palabra (sin acentos, minúsculas) → emoji. Raíces largas primero para no confundir.
KEYWORDS = [
    ("dinero", "💰"), ("millon", "💰"), ("dolar", "💵"), ("pesos", "💵"), ("gratis", "🆓"), ("precio", "🏷️"),
    ("barato", "🤑"), ("caro", "💸"), ("pagar", "💸"), ("vender", "💸"), ("venta", "💸"),
    ("fuego", "🔥"), ("brutal", "🔥"), ("increible", "🤯"), ("locura", "🤯"), ("loco", "🤪"),
    ("risa", "😂"), ("jaja", "😂"), ("chiste", "😂"), ("gracioso", "😂"),
    ("miedo", "😱"), ("terror", "😱"), ("susto", "😱"), ("peligro", "⚠️"), ("cuidado", "⚠️"),
    ("amor", "❤️"), ("corazon", "❤️"), ("encanta", "😍"), ("hermos", "😍"), ("bonit", "😍"),
    ("triste", "😢"), ("llorar", "😭"), ("enojo", "😡"), ("odio", "😡"), ("furia", "😡"),
    ("juego", "🎮"), ("jugar", "🎮"), ("gamer", "🎮"), ("consola", "🎮"), ("play", "🎮"),
    ("coche", "🚗"), ("carro", "🚗"), ("auto", "🚗"), ("moto", "🏍️"), ("avion", "✈️"), ("viaje", "✈️"),
    ("musica", "🎵"), ("cancion", "🎵"), ("video", "🎬"), ("pelicula", "🎬"), ("youtube", "▶️"),
    ("telefono", "📱"), ("celular", "📱"), ("computadora", "💻"), ("internet", "🌐"), ("idea", "💡"),
    ("secreto", "🤫"), ("verdad", "✅"), ("mentira", "❌"), ("error", "❌"), ("problema", "⚠️"),
    ("ganar", "🏆"), ("gane", "🏆"), ("victoria", "🏆"), ("campeon", "🏆"), ("perder", "💀"), ("muerte", "💀"),
    ("muerto", "💀"), ("tiempo", "⏰"), ("rapido", "⚡"), ("energia", "⚡"), ("comida", "🍔"), ("hambre", "🍔"),
    ("cafe", "☕"), ("fiesta", "🎉"), ("feliz", "😄"), ("cumple", "🎂"), ("regalo", "🎁"), ("sorpresa", "😲"),
    ("pregunta", "❓"), ("atencion", "👀"), ("ojo", "👀"), ("escucha", "👂"),
    ("trabajo", "💼"), ("escuela", "🏫"), ("casa", "🏠"), ("mundo", "🌎"), ("japon", "🇯🇵"), ("mexico", "🇲🇽"),
    ("futbol", "⚽"), ("gol", "⚽"), ("perro", "🐶"), ("gato", "🐱"), ("bomba", "💣"), ("explot", "💥"),
    ("golpe", "💥"), ("pistola", "🔫"), ("arma", "🔫"), ("policia", "🚓"), ("record", "📈"), ("subir", "📈"),
    ("bajar", "📉"), ("crisis", "📉"), ("estrella", "⭐"), ("rey", "👑"), ("reina", "👑"), ("cerebro", "🧠"),
    ("inteligen", "🧠"), ("robot", "🤖"), ("magia", "✨"), ("perfecto", "👌"), 
]
_KW = sorted(KEYWORDS, key=lambda kv: -len(kv[0]))


def _norm(word: str) -> str:
    w = unicodedata.normalize("NFD", str(word).lower())
    w = "".join(c for c in w if unicodedata.category(c) != "Mn")
    return re.sub(r"[^a-z0-9ñ]", "", w)


def for_word(word: str) -> str | None:
    w = _norm(word)
    if len(w) < 3:
        return None
    for root, emo in _KW:
        if len(root) <= 4 and root != "jaja":
            # raíces cortas: solo la palabra exacta o su plural ("auto" sí, "automático" no; "mal" no es "maleta")
            if w in (root, root + "s", root + "es"):
                return emo
        elif w.startswith(root):
            return emo
    return None


def plan(groups: list[list[dict]]) -> list[dict]:
    """Un emoji por grupo de subtítulo cuya frase dice una palabra clave (uno cada MIN_GAP_S como mucho).
    groups: los grupos de palabras de los subtítulos (con start/end)."""
    out, last = [], -1e9
    for g in groups:
        if not g:
            continue
        for w in g:
            emo = for_word(w.get("word", ""))
            if emo and float(w["start"]) - last >= MIN_GAP_S:
                out.append({"start": round(float(w["start"]), 3), "end": round(float(g[-1]["end"]), 3),
                            "emoji": emo, "word": w.get("word", "")})
                last = float(w["start"])
                break
    return out


def font_path() -> str | None:
    for f in FONT_CANDIDATES:
        if Path(f).exists():
            return f
    return None


def render_png(emo: str, size: int, out: Path) -> Path | None:
    """Dibuja el emoji a color en un PNG transparente de `size`×`size`. None si no hay fuente de emojis."""
    fp = font_path()
    if not fp:
        return None
    try:
        from PIL import Image, ImageDraw, ImageFont
        # Noto Color Emoji es de mapas de bits: solo carga a 109 px y se escala después
        base = 109 if "Noto" in fp else max(64, size)
        font = ImageFont.truetype(fp, base)
        canvas = Image.new("RGBA", (base * 2, base * 2), (0, 0, 0, 0))
        ImageDraw.Draw(canvas).text((base // 2, base // 2), emo, font=font, embedded_color=True)
        box = canvas.getbbox()
        if not box:
            return None
        img = canvas.crop(box)
        # ¿salió a color? si todos los píxeles visibles son grises, la fuente no tiene color: mejor no ponerlo
        px = [p for p in img.getdata() if p[3] > 128]
        if px and all(abs(p[0] - p[1]) < 8 and abs(p[1] - p[2]) < 8 for p in px[:: max(1, len(px) // 400)]):
            return None
        img.thumbnail((size, size), Image.LANCZOS)
        img.save(out)
        return out
    except Exception:  # noqa: BLE001 — sin emoji el video sale igual
        return None
