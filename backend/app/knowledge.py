"""Lo que tu IA sabe de edición y lo que aprende de ti.

1. Guía de edición (data/knowledge/guia_edicion.md): principios de edición viral que la IA lee antes de editar.
   Es tuya: la puedes editar en la app (Studio → Estilos aprendidos).
2. Estilos aprendidos: el análisis ("por qué funciona y qué copiar") del video de referencia elegido.
3. Tus opiniones: 👍/👎 y notas sobre videos y clips generados. Las últimas se inyectan en cada edición nueva,
   así la IA corrige lo que no te gustó y repite lo que sí.
"""
from __future__ import annotations

import json

from . import config, db

GUIDE_PATH = config.DATA_DIR / "knowledge" / "guia_edicion.md"

DEFAULT_GUIDE = """# Guía de edición viral (léela antes de editar)

## Principio
Cada segundo debe ganarse el siguiente. Si un tramo no aporta emoción, información o risa, se corta.

## Gancho (primeros 3-10 s)
- Abre con el momento más fuerte (reacción, jugada, frase polémica) ANTES de cualquier contexto.
- Texto grande que genere curiosidad ("NO ESPERABA ESTO", "MIRA LO QUE PASA").

## Ritmo
- Cortes secos (jump cuts) en silencios, menús, cargas, esperas y repeticiones.
- Interrupción de patrón cada 5-8 s en contenido corto y cada 15-30 s en largo: zoom, cambio de plano,
  texto, efecto de sonido o cambio de encuadre.
- Planos largos de gameplay: empuje lento de cámara para que nunca se vean estáticos.

## Énfasis
- Golpe de zoom (punch-in) + efecto de impacto en: kills, fails, sustos, remates de chistes, cifras.
- Sacudida de pantalla solo en impactos fuertes (explosión, grito, muerte), no más de 1 cada 20 s.
- Zoom a la cámara del streamer en sus reacciones (risa, grito, cara de incredulidad): 1-2 s.

## Texto en pantalla
- Memes: 1-3 palabras en mayúsculas que exageran la emoción (¡NOOO!, CLUTCH, F, ¿QUÉ?, LO LOGRÓ).
- Callouts: datos o frases clave de lo que se dice (máx. 5 palabras).
- Nunca tapes la acción principal ni pongas texto sobre texto.

## Sonido
- Whoosh en transiciones, impacto en golpes de zoom, pop al aparecer texto.
- La música de fondo baja cuando habla el creador; sin música si el estilo no la usa.
- Evita música comercial con derechos en videos monetizados (reclamos de Content ID).

## Lo que NO se hace
- Efectos en cada corte (cansa); textos genéricos ("WOW") repetidos; transiciones lentas en contenido rápido.
"""

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS feedback (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    kind       TEXT NOT NULL,            -- youtube | clip | studio
    ref_id     INTEGER,
    rating     INTEGER NOT NULL,         -- 1 me gusta, -1 no me gusta, 0 solo nota
    note       TEXT,
    context    TEXT,                     -- título, modelo, estilo…
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
"""


def ensure_schema() -> None:
    with db.get_conn() as conn:
        conn.executescript(SCHEMA_SQL)


def guide() -> str:
    try:
        return GUIDE_PATH.read_text(encoding="utf-8")
    except FileNotFoundError:
        return DEFAULT_GUIDE


def save_guide(text: str | None) -> str:
    GUIDE_PATH.parent.mkdir(parents=True, exist_ok=True)
    if not text or not text.strip():
        GUIDE_PATH.unlink(missing_ok=True)
        return DEFAULT_GUIDE
    GUIDE_PATH.write_text(text.strip() + "\n", encoding="utf-8")
    return guide()


def add_feedback(kind: str, ref_id: int | None, rating: int, note: str | None, context: dict | None) -> dict:
    ensure_schema()
    with db.get_conn() as conn:
        cur = conn.execute("INSERT INTO feedback (kind, ref_id, rating, note, context) VALUES (?, ?, ?, ?, ?)",
                           (kind, ref_id, max(-1, min(1, int(rating))), (note or "").strip()[:600] or None,
                            json.dumps(context or {}, ensure_ascii=False)))
    if kind == "clip" and ref_id and int(rating) != 0 and not (context or {}).get("auto"):
        try:  # 👍/👎 en un clip: también es una foto para el perfil de gusto
            from . import taste
            taste.record(int(ref_id), int(rating), "feedback", note)
        except Exception:  # noqa: BLE001
            pass
    return {"id": cur.lastrowid}


def list_feedback(limit: int = 30, kind: str | None = None) -> list[dict]:
    ensure_schema()
    q, args = "SELECT * FROM feedback", []
    if kind:
        q, args = q + " WHERE kind = ?", [kind]
    with db.get_conn() as conn:
        rows = conn.execute(q + " ORDER BY id DESC LIMIT ?", (*args, limit)).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["context"] = json.loads(d["context"] or "{}")
        out.append(d)
    return out


def delete_feedback(fid: int) -> None:
    ensure_schema()
    with db.get_conn() as conn:
        conn.execute("DELETE FROM feedback WHERE id = ?", (fid,))


def style_notes(style_id: int | None, max_chars: int = 1600) -> str:
    if not style_id:
        return ""
    from .studio import learn
    learn.ensure_schema()
    with db.get_conn() as conn:
        r = conn.execute("SELECT name, report FROM learned_styles WHERE id = ?", (style_id,)).fetchone()
    if not r or not r["report"]:
        return ""
    return f"## Estilo aprendido a imitar: {r['name']}\n{r['report'].strip()[:max_chars]}"


def prompt_context(kind: str, style_id: int | None = None, max_chars: int = 4200) -> str:
    """Bloque de conocimiento para los prompts. Prioridad cuando falta espacio: lo aprendido de ti
    (ejemplos y opiniones) > estilo aprendido > guía general (que se recorta al espacio restante)."""
    learned: list[str] = []
    if kind == "clip":
        try:
            from . import taste
            tb = taste.prompt_block(min(1400, max_chars // 2))
            if tb:
                learned.append(tb)
        except Exception:  # noqa: BLE001 — sin perfil se sigue con la guía
            pass
    fb = [f for f in list_feedback(40) if (f["note"] or f["rating"])
          and not (f["note"] or "").startswith(("aprobó el clip", "descartó el clip"))]  # ya van como ejemplos
    rel = [f for f in fb if f["kind"] == kind] + [f for f in fb if f["kind"] != kind]
    likes, dislikes = [], []
    for f in rel[:14]:
        label = f["context"].get("title") or f["kind"]
        txt = f"- {label}: {f['note']}" if f["note"] else f"- {label}"
        (likes if f["rating"] > 0 else dislikes).append(txt[:220])
    if likes or dislikes:
        block = "## Opiniones del creador sobre ediciones anteriores (OBLIGATORIO tenerlas en cuenta)"
        if likes:
            block += "\nLe gustó:\n" + "\n".join(likes[:6])
        if dislikes:
            block += "\nNo le gustó / quiere cambiar:\n" + "\n".join(dislikes[:8])
        learned.append(block[:1200])
    sn = style_notes(style_id)
    head = "\n\n".join(learned + ([sn] if sn else []))
    room = max(700, max_chars - len(head) - 4)
    return ("\n\n".join([guide().strip()[:min(2400, room)]] + ([head] if head else [])))[:max_chars + 700]
