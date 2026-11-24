"""Perfil de estilo (marca propia) y análisis de ritmo de un video de referencia."""
from __future__ import annotations

import re
from pathlib import Path

DEFAULT_STYLE = {
    # Subtítulos
    "caption_style": "karaoke",        # karaoke | simple | clean (videos largos) | none
    "caption_font": "Arial Black",
    "caption_size": 88,
    "caption_color": "#FFFFFF",
    "highlight_color": "#FFE500",
    "caption_position": "auto",        # auto (según el encuadre) | bottom | middle | top
    "caption_uppercase": True,
    "caption_max_words": 3,
    # Títulos y textos destacados
    "title_font": "Arial Black",
    "title_color": "#FFFFFF",
    "title_bg": "#7C5CFF",
    "callout_color": "#FFE500",
    # Ritmo de edición
    "silence_threshold": 0.45,         # pausas más largas que esto se cortan (s)
    "remove_fillers": True,
    "zoom_amount": 1.12,               # zoom de los cortes "punch-in"
    "zoom_every": 1,                   # cada cuántos segmentos alterna el zoom
    "zoom_per_min": 0.0,               # zooms por minuto medidos en tu referencia (0 = no medido)
    "zoom_hold_s": 0.0,                # cuánto se mantiene cada zoom (s) en tu referencia
    "zoom_kind": "",                   # punch (golpe) | ramp (progresivo) | none — "" = no medido
    "sfx_density": 0.35,               # probabilidad de whoosh en cambios de plano (0-1)
    "callouts_per_min": 4,             # textos destacados por minuto (IA)
    "transition_style": "dinamico",    # dinamico (zoom/destello/glitch/barrido) | suave (fundidos) | cortes
    # Tomas de tus videos (b-roll)
    "broll_volume": 0.0,               # volumen del audio original de las tomas (0 = mudo)
    "broll_fit": "cover",              # cover (llena la pantalla) | blur (completo con fondo desenfocado)
    # Audio
    "music_volume": 0.14,
    "duck_music": True,
    "voice_volume": 1.0,
    "voice_mode": "original",          # original | realzar (voz al frente con Demucs) | solo_voz (sin música de fondo)
    "pro_audio": True,                 # compresión de voz + volumen estándar -14 LUFS
    "clean_voice": False,              # reducción de ruido (solo tomas de voz; en gameplay apaga sonidos)
    "beat_sync": True,                 # efectos al ritmo de la música
    # Sonido aprendido de tus referencias (earshot): 0/"" = sin aprender
    "sfx_per_min": 0.0,                # efectos de sonido por minuto en tus referencias
    "sound_roles": "",                 # tipos de efecto que usan (transicion,impacto,texto…) por orden
    "sfx_on_cuts": False,
    "memes": True,                     # textos meme/sacudidas del director (no en videos largos serios)
    "fx_gap_s": 0.0,                   # separación mínima entre zooms/efectos de cámara (videos largos)              # ponen un whoosh/sonido en los cortes
    # Fondo para proyectos de solo voz
    "bg_color_1": "#1B1035",
    "bg_color_2": "#7C5CFF",
}

HEX = re.compile(r"^#[0-9A-Fa-f]{6}$")

LIMITS = {
    "caption_size": (56, 140), "caption_max_words": (1, 8), "silence_threshold": (0.15, 2.0),
    "zoom_amount": (1.0, 1.5), "zoom_every": (1, 6), "zoom_per_min": (0.0, 30.0), "zoom_hold_s": (0.0, 8.0), "sfx_density": (0.0, 1.0),
    "callouts_per_min": (0, 12), "sfx_per_min": (0.0, 60.0), "music_volume": (0.0, 1.0), "voice_volume": (0.2, 2.0), "broll_volume": (0.0, 1.0),
}
CHOICES = {
    "caption_style": {"karaoke", "simple", "clean", "none"},
    "caption_position": {"auto", "bottom", "middle", "top"},
    "transition_style": {"dinamico", "suave", "cortes"},
    "broll_fit": {"cover", "blur"},
    "voice_mode": {"original", "realzar", "solo_voz"},
    "zoom_kind": {"", "punch", "ramp", "none"},
}


def clean_style(raw: dict | None) -> dict:
    out = dict(DEFAULT_STYLE)
    for k, v in (raw or {}).items():
        if k not in DEFAULT_STYLE:
            continue
        default = DEFAULT_STYLE[k]
        try:
            if k in CHOICES:
                if v in CHOICES[k]:
                    out[k] = v
            elif isinstance(default, bool):
                out[k] = bool(v)
            elif isinstance(default, (int, float)):
                lo, hi = LIMITS.get(k, (float("-inf"), float("inf")))
                val = type(default)(float(v)) if isinstance(default, float) else int(float(v))
                out[k] = max(lo, min(hi, val))
            elif k.endswith(("_color", "_bg", "_1", "_2")):
                if isinstance(v, str) and HEX.match(v):
                    out[k] = v.upper()
            elif isinstance(v, str) and v.strip():
                out[k] = v.strip()[:60]
        except (TypeError, ValueError):
            continue
    return readable(out)


def _lum(hex_color: str) -> float:
    h = hex_color.lstrip("#")
    try:
        r, g, b = (int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))
    except (ValueError, IndexError):
        return 1.0
    lin = lambda c: c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4  # noqa: E731
    return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b)


def contrast(a: str, b: str) -> float:
    la, lb = _lum(a), _lum(b)
    return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)


def readable(st: dict) -> dict:
    """Que el texto de títulos y rótulos se lea: si la letra y el fondo de la caja se parecen (p. ej. blanco sobre
    blanco, como pasaba con un estilo aprendido), la letra pasa a negro o blanco, lo que contraste más."""
    bg, fg = st.get("title_bg"), st.get("title_color")
    if isinstance(bg, str) and isinstance(fg, str) and HEX.match(bg) and HEX.match(fg) and contrast(fg, bg) < 3.0:
        st["title_color"] = "#111111" if contrast("#111111", bg) >= contrast("#FFFFFF", bg) else "#FFFFFF"
    return st


def ass_color(hex_color: str, alpha: int = 0) -> str:
    """#RRGGBB → &HAABBGGRR (formato ASS)."""
    h = hex_color.lstrip("#")
    r, g, b = h[0:2], h[2:4], h[4:6]
    return f"&H{alpha:02X}{b}{g}{r}".upper()


def analyze_reference(path: Path, duration: float, words: list[dict] | None = None) -> dict:
    """Saca el ritmo de un video de referencia: cortes por minuto y velocidad del habla."""
    from ..ai.audio import detect_scenes

    scenes = detect_scenes(path, threshold=0.3)
    minutes = max(duration / 60, 0.1)
    cuts_per_min = len(scenes) / minutes
    avg_shot = duration / (len(scenes) + 1)
    result = {"cuts_per_min": round(cuts_per_min, 1), "avg_shot_seconds": round(avg_shot, 2)}
    changes = {}
    # Mucho ritmo → zoom en cada segmento, más whooshes, cortes de pausa más agresivos
    if cuts_per_min >= 20:
        changes.update(zoom_every=1, sfx_density=0.5, silence_threshold=0.3, zoom_amount=1.15)
    elif cuts_per_min >= 10:
        changes.update(zoom_every=1, sfx_density=0.35, silence_threshold=0.4)
    elif cuts_per_min >= 4:
        changes.update(zoom_every=2, sfx_density=0.2, silence_threshold=0.55)
    else:
        changes.update(zoom_every=3, sfx_density=0.1, silence_threshold=0.7, zoom_amount=1.08)
    if words:
        wpm = len(words) / minutes
        result["words_per_min"] = round(wpm)
        changes["caption_max_words"] = 2 if wpm > 190 else 3 if wpm > 140 else 4
    result["changes"] = changes
    return result
