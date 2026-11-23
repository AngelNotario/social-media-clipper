"""Tus instrucciones del brief, primero.

Antes se aplicaban al FINAL (después de todo) pidiéndole a la IA operaciones sobre el timeline completo; en un video
de 36 minutos contestaba «No hice ningún cambio» y se perdían. Ahora, antes de editar, la IA las traduce a ajustes
del proceso (subtítulos, música, zooms, memes, transiciones, cuántos recursos, pantalla verde) que guían cada paso.
Solo lo que es un cambio puntual («pon un texto cuando digo X») queda para el paso final, de uno en uno.
"""
from __future__ import annotations

from ..ai import llm

CHOICE = lambda *opts: {"type": "string", "enum": [*opts, "igual"]}  # noqa: E731

SCHEMA = {
    "type": "object",
    "properties": {
        "subtitulos": CHOICE("si", "no"),
        "musica": CHOICE("si", "no"),
        "zooms": CHOICE("mas", "menos", "ninguno"),
        "memes": CHOICE("si", "no"),
        "transiciones": CHOICE("dinamico", "suave", "cortes"),
        "recursos": CHOICE("mas", "menos", "ninguno"),
        "pantalla_verde": CHOICE("si", "no"),
        "puntuales": {"type": "array", "items": {"type": "string"}},
        "resumen": {"type": "string"},
    },
    "required": ["subtitulos", "musica", "zooms", "memes", "transiciones", "recursos", "pantalla_verde",
                 "puntuales", "resumen"],
}

SYSTEM = """Traduces las instrucciones de edición de un creador de video a AJUSTES del proceso automático.
Responde solo JSON. Para cada ajuste elige un valor SOLO si la instrucción lo pide (directa o claramente);
si no lo menciona, "igual". No inventes preferencias.
- subtitulos: si/no
- musica: si (poner música de fondo) / no (quitarla)
- zooms: mas / menos / ninguno
- memes: si/no (textos de reacción tipo meme)
- transiciones: dinamico / suave / cortes (cortes secos)
- recursos: mas / menos / ninguno (imágenes, logos y clips de internet o de su biblioteca)
- pantalla_verde: si (quitar fondo verde y poner otro, p. ej. un gameplay) / no
- puntuales: cambios en un MOMENTO concreto del video, cada uno como frase corta en español
  (p. ej. "pon el texto 'ERROR #1' cuando digo el primer error"). Lo general NO va aquí.
- resumen: una frase en español con lo que entendiste."""


def plan(instructions: str, url: str, model: str) -> dict | None:
    text = (instructions or "").strip()
    if not text:
        return None
    try:
        d = llm.chat_json(url, model, SYSTEM, f"INSTRUCCIONES DEL CREADOR:\n{text[:3000]}", SCHEMA,
                          num_ctx=4096, max_tokens=500, timeout=300)
    except Exception:  # noqa: BLE001 — sin plan, las instrucciones siguen yendo al paso final
        return None
    d["puntuales"] = [str(x).strip()[:200] for x in (d.get("puntuales") or []) if str(x).strip()][:12]
    return d


def apply(d: dict | None, b: dict, st: dict, pace: dict) -> tuple[list[str], dict]:
    """Aplica el plan a brief, estilo y ritmo. Devuelve (qué cambió, banderas para pasos siguientes)."""
    flags = {"no_zoom": False, "resources": 1.0}
    if not d:
        return [], flags
    notes = []
    if d.get("subtitulos") in ("si", "no"):
        pace["captions_on"] = d["subtitulos"] == "si"
        b["captions"] = "on" if pace["captions_on"] else "off"
        notes.append("subtítulos " + ("encendidos" if pace["captions_on"] else "apagados"))
    if d.get("musica") == "no":
        b["music"], st["music_volume"] = "off", 0.0
        notes.append("sin música")
    elif d.get("musica") == "si":
        b["music"] = "on"
        st["music_volume"] = max(float(st.get("music_volume") or 0), 0.06)
        notes.append("con música de fondo")
    z = d.get("zooms")
    if z == "ninguno":
        st["zoom_amount"], flags["no_zoom"] = 1.0, True
        notes.append("sin zooms")
    elif z == "menos":
        st["zoom_every"] = min(6, int(st.get("zoom_every") or 1) + 2)
        notes.append("menos zooms")
    elif z == "mas":
        st["zoom_every"], st["zoom_amount"] = 1, max(float(st.get("zoom_amount") or 1.0), 1.12)
        notes.append("más zooms")
    if d.get("memes") in ("si", "no"):
        st["memes"] = d["memes"] == "si"
        notes.append("con memes" if st["memes"] else "sin memes")
    if d.get("transiciones") in ("dinamico", "suave", "cortes"):
        st["transition_style"] = d["transiciones"]
        notes.append(f"transiciones: {d['transiciones']}")
    r = d.get("recursos")
    if r in ("mas", "menos", "ninguno"):
        flags["resources"] = {"mas": 1.5, "menos": 0.6, "ninguno": 0.0}[r]
        pace["broll_every_s"] = 9999 if r == "ninguno" else pace.get("broll_every_s", 20) / flags["resources"]
        notes.append({"mas": "más recursos", "menos": "menos recursos", "ninguno": "sin recursos"}[r])
    if d.get("pantalla_verde") in ("si", "no"):
        b["chroma"] = "on" if d["pantalla_verde"] == "si" else "off"
        notes.append("pantalla verde " + ("activada" if b["chroma"] == "on" else "desactivada"))
    return notes, flags
