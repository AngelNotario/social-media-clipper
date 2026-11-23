"""Juez estricto con bucle de retroalimentación iterativa.

Compara la edición contra los videos de referencia aprendidos (estilo) y, si no
aprueba, rehace la edición aplicando correcciones específicas de qué está mal,
hasta que el juez da el visto bueno o se agotan las rondas.

Capas de juicio:
1. Métricas de estilo (style_judge): cambios visuales, recursos, textos, zooms, SFX, música vs referencia.
2. Contenido (IA de texto): cada texto/recurso en el timeline debe tener soporte en lo que se dice
   en ese instante (no inventar). Los recursos de internet deben encajar con el tema de ese momento.
3. Visual (IA de visión, opcional): compara frames clave de la edición contra el estilo de referencia.
4. Posición y encuadre: ningún recurso debe tapar el rostro ni los subtítulos clave.

Si la nota es < STRICT_GOAL se genera un plan de correcciones (quitar, mover, cambiar texto,
ajustar escala/pos, ajustar parámetros) y se vuelve a juzgar. Hasta MAX_ROUNDS.
"""
from __future__ import annotations

import base64
import io
import json
import math
import subprocess
from pathlib import Path
from typing import Callable

from PIL import Image, ImageDraw

from .. import config, db
from ..ai import llm
from . import timeline as tlm
from . import style_judge as sj
from . import assets
from .agent import callout_is_said

STRICT_GOAL = 90.0
MAX_ROUNDS = 8

# ---------------- utilidades de frames ----------------

def _extract_source_frames(source_path: str | None, times: list[float], work: Path) -> list[Path]:
    """Extrae un JPG por tiempo del video fuente. Devuelve las rutas que existen."""
    out: list[Path] = []
    if not source_path or not Path(source_path).exists():
        return out
    work.mkdir(parents=True, exist_ok=True)
    for i, t in enumerate(times):
        f = work / f"sj_{i:02d}.jpg"
        r = subprocess.run(
            ["ffmpeg", "-y", "-v", "error", "-ss", f"{max(0.0, t):.2f}", "-i", source_path,
             "-frames:v", "1", "-vf", "scale=640:-2", "-q:v", "4", str(f)],
            capture_output=True, timeout=120)
        if f.exists():
            out.append(f)
    return out


def _edit_key_times(tl: dict, n: int = 6) -> list[float]:
    """Tiempos representativos del timeline: inicio, medio, fin y donde hay recursos/textos."""
    dur = float(tl.get("duration") or 0)
    if dur <= 0:
        return []
    hits = set()
    for k in (0.0, dur / 2, max(0.0, dur - 1.0)):
        hits.add(round(k, 2))
    for b in tl["tracks"].get("broll") or []:
        hits.add(round(float(b.get("start", 0)) + 0.5, 2))
    for t in tl["tracks"].get("text") or []:
        hits.add(round(float(t.get("start", 0)) + 0.3, 2))
    times = sorted(h for h in hits if 0 <= h <= dur)
    if len(times) > n:
        step = dur / (n - 1) if n > 1 else dur
        times = [round(step * i, 2) for i in range(n)]
    return times


# ---------------- juez de contenido (texto + recursos vs lo que se dice) ----------------

def _asset_theme(a: dict | None) -> str:
    if not a:
        return ""
    parts = [str(a.get("title", "")), str(a.get("tags", ""))]
    return " | ".join(p for p in parts if p)[:120]


def _words(s: str) -> list[str]:
    import re
    import unicodedata
    s = unicodedata.normalize("NFD", str(s or "").lower())
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    return re.findall(r"[a-z0-9ñ]+", s)


def _content_judge(tl: dict, words: list[dict], dur: float, by_id: dict) -> dict:
    """Revisa cada texto y recurso del timeline contra la transcripción. Devuelve problemas."""
    problemas: list[dict] = []
    seen_text_times = [t["start"] for t in tl["tracks"].get("text") or []]

    for t in tl["tracks"].get("text") or []:
        txt = str(t.get("text", "")).strip()
        t0 = float(t.get("start", 0))
        if not txt:
            continue
        if t0 < 2.5 or t.get("kind") in ("title", "meme"):
            continue  # el título y las reacciones (meme) no tienen que decirse literalmente
        if not callout_is_said(tl, txt, t0, window=6.0):
            problemas.append({
                "tipo": "texto_sin_soporte",
                "id": t["id"],
                "tiempo": round(t0, 2),
                "texto": txt[:60],
                "por_que": "el texto no se dice en ese momento (la IA lo inventó)",
                "accion": "quitar",
            })
        elif len(txt.split()) > 6 and t.get("kind") in ("callout", "meme"):
            problemas.append({
                "tipo": "texto_muy_largo",
                "id": t["id"],
                "tiempo": round(t0, 2),
                "texto": txt[:60],
                "por_que": "texto demasiado grande para un rótulo rápido",
                "accion": "acortar",
            })

    for b in tl["tracks"].get("broll") or []:
        t0 = float(b.get("start", 0))
        t1 = float(b.get("end", t0 + 1))
        mode = b.get("mode") or "full"
        a = by_id.get(b.get("asset_id")) if b.get("asset_id") else None
        theme = _asset_theme(a)
        said = " ".join(w["word"] for w in words if t0 - 2 <= w["start"] <= t1 + 2)
        # Antes se marcaba TODO recurso a pantalla completa (y se borraba). Ahora solo si su tema no comparte ninguna
        # palabra con lo que dices alrededor.
        theme_words = {w for w in _words(theme) if len(w) >= 4}
        if theme_words and said and not theme_words & set(_words(said)):
            problemas.append({
                "tipo": "recurso_sin_relacion",
                "id": b["id"],
                "asset_id": b.get("asset_id"),
                "tiempo": round(t0, 2),
                "tema": theme,
                "por_que": f"el recurso ({theme}) no tiene que ver con lo que dices: «{said[:60]}»",
                "accion": "quitar",
            })
        # tarjeta que tapa el centro (pos cerca de 0.5,0.5) en video con rostro
        pos = b.get("pos") or {}
        if mode in ("card", "pip") and abs(float(pos.get("x", 0.5)) - 0.5) < 0.15 and abs(float(pos.get("y", 0.5)) - 0.5) < 0.25:
            problemas.append({
                "tipo": "tarjeta_tapa_centro",
                "id": b["id"],
                "tiempo": round(t0, 2),
                "por_que": "la tarjeta queda en el centro y puede tapar el rostro",
                "accion": "mover",
                "nuevo": {"pos": {"x": 0.85, "y": 0.85}},
            })

    return problemas


# ---------------- juez visual (visión contra referencia) ----------------

def _style_summary(prof: dict) -> str:
    """Resumen legible del estilo aprendido para el juez de visión."""
    d = prof.get("design") or {}
    e = prof.get("edit") or {}
    el = prof.get("elements") or {}
    parts = []
    if d.get("layout"):
        parts.append(f"composición: {d['layout']}")
    if d.get("caption_position"):
        parts.append(f"subtítulos: {d['caption_position']}")
    if d.get("words_on_screen"):
        parts.append(f"pantallas: hasta {d['words_on_screen']} palabras a la vez")
    if d.get("uppercase") is not None:
        parts.append(f"mayúsculas: {'sí' if d['uppercase'] else 'no'}")
    if el.get("card_ratio") is not None:
        parts.append(f"tarjetas: {int(el['card_ratio']*100)}% de los recursos")
    if el.get("fullscreen_ratio") is not None:
        parts.append(f"pantalla completa: {int(el['fullscreen_ratio']*100)}% de los recursos")
    if e.get("cuts_per_min"):
        parts.append(f"cortes: {e['cuts_per_min']} por minuto")
    return "; ".join(parts) if parts else "estilo general de edición viral"


def _visual_judge(tl: dict, source_path: str | None, ref_style: dict, work: Path) -> dict | None:
    """Compara frames de la edición contra el estilo de referencia con IA de visión."""
    vmodel = db.get_setting("vision_model") or "qwen3.6:35b-a3b"
    url = db.get_setting("ollama_url")
    try:
        st = llm.status(url)
        if not (st["available"] and vmodel in st["models"]):
            return None
    except Exception:
        return None

    times = _edit_key_times(tl, n=6)
    frames = _extract_source_frames(source_path, times, work)
    if not frames:
        return None

    buf = io.BytesIO()
    _grid_vertical(frames, cell=(480, 270), out=buf)
    b64 = base64.b64encode(buf.getvalue()).decode()

    system = ("Eres un editor de video senior. La imagen muestra fotogramas clave de una EDICIÓN "
              "hecha por tu app. Debes calificar de 0 a 100 el ESTILO DE EDICIÓN (no el tema): "
              "ritmo, cantidad de recursos en pantalla, posición de tarjetas, subtítulos, composición. "
              f"Referencia del estilo que se busca imitar: {ref_style}. "
              "Responde solo JSON {\"score\": 0-100, \"problemas\": [breves en español], \"notas\": \"...\"}.")
    try:
        r = llm.chat_json(url, vmodel, system, "Califica esta edición.",
                          {"type": "object", "properties": {
                              "score": {"type": "number"},
                              "problemas": {"type": "array", "items": {"type": "string"}},
                              "notas": {"type": "string"}},
                           "required": ["score", "problemas", "notas"]},
                          images=[b64], num_ctx=4096, keep_alive="2m", timeout=300, max_tokens=400)
    except Exception:
        return None

    problemas = []
    for p in r.get("problemas", []) or []:
        problemas.append({"tipo": "visual", "por_que": str(p)[:120], "accion": "ajustar"})
    return {"score": _f(r.get("score")) or 0.0, "problemas": problemas, "notas": str(r.get("notas") or "")[:200]}


def _grid_vertical(frames: list[Path], cell: tuple[int, int], out: io.BytesIO) -> None:
    from PIL import Image, ImageDraw
    n = len(frames)
    cols = min(3, n)
    rows = math.ceil(n / cols)
    W, H = cell[0] * cols + 12 * (cols + 1), cell[1] * rows + 40 + 8 * rows
    g = Image.new("RGB", (W, H), (20, 20, 24))
    d = ImageDraw.Draw(g)
    d.text((12, 10), "EDICION (fotogramas clave)", fill=(255, 255, 255))
    for i, f in enumerate(frames):
        c, r = i % cols, i // cols
        try:
            im = Image.open(f).convert("RGB")
        except Exception:
            continue
        im.thumbnail(cell)
        x = 12 * (c + 1) + c * cell[0] + (cell[0] - im.width) // 2
        y = 40 + 8 * (r + 1) + r * cell[1] + (cell[1] - im.height) // 2
        g.paste(im, (x, y))
        d.text((x, y + cell[1] + 2), f"{i+1}", fill=(200, 200, 200))
    g.save(out, "JPEG", quality=85)


def _f(v):
    try:
        x = float(v)
        return x if math.isfinite(x) else None
    except (TypeError, ValueError):
        return None


# ---------------- aplicación de correcciones ----------------

def _apply_corrections(tl: dict, st: dict, correcciones: list[dict], words: list[dict]) -> tuple[bool, set]:
    """Aplica correcciones al timeline en su lugar. Devuelve (cambió algún parámetro, assets quitados)."""
    changed, dropped = False, set()
    for c in correcciones:
        if c.get("accion") == "quitar" and str(c.get("id", "")).startswith("b"):
            b = next((x for x in tl["tracks"].get("broll", []) if x["id"] == c["id"]), None)
            if b and b.get("asset_id"):
                dropped.add(b["asset_id"])
        accion = c.get("accion")
        try:
            if accion == "quitar":
                track = "broll" if c["id"].startswith("b") else ("text" if c["id"].startswith("t") else None)
                if track and track in tl["tracks"]:
                    tl["tracks"][track] = [x for x in tl["tracks"][track] if x["id"] != c["id"]]
            elif accion == "mover":
                track = "broll" if c["id"].startswith("b") else ("text" if c["id"].startswith("t") else None)
                it = next((x for x in tl["tracks"].get(track or [], []) if x["id"] == c["id"]), None) if track else None
                if it and "nuevo" in c:
                    if "pos" in c["nuevo"]:
                        it["pos"] = c["nuevo"]["pos"]
                    if "scale" in c["nuevo"]:
                        it["scale"] = float(c["nuevo"]["scale"])
            elif accion == "acortar":
                it = next((x for x in tl["tracks"].get("text", []) if x["id"] == c["id"]), None)
                if it:
                    txt = str(it.get("text", ""))
                    it["text"] = " ".join(txt.split()[:4])
            elif accion == "ajustar_parametro":
                key = c.get("key")
                val = c.get("valor")
                if key in ("visual_per_min", "labels_per_min", "zoom_per_min") and st.get(key) != float(val):
                    st[key] = float(val)
                    changed = True
        except Exception:
            continue
    return changed, dropped


# ---------------- bucle estricto principal ----------------

def run_strict(tl: dict, st: dict, ctx: dict, source_path: str | None = None,
               progress: Callable[[float, str], None] = lambda p, s: None,
               replace: Callable[[dict, set], None] | None = None) -> dict:
    """Bucle estricto: juzga y corrige la edición hasta aprobar o agotar rondas.

    ctx debe traer style_id (referencia aprendida), video_type, edit_style.
    Devuelve {"aprobado": bool, "ronda": int, "score": float, "reporte": dict}.
    """
    style_id = ctx.get("style_id")
    edit_style = ctx.get("edit_style") or ""
    video_type = ctx.get("video_type") or "long"
    dur = float(tl.get("duration") or 0)
    words = tl.get("words") or []
    targets = sj.targets_for(style_id, video_type, edit_style)
    by_id = {a["id"]: a for a in assets.list_assets() if a.get("available")}

    # Estilo de referencia para el juez visual
    ref_style = {}
    if style_id:
        try:
            with db.get_conn() as conn:
                r = conn.execute("SELECT profile FROM learned_styles WHERE id = ?", (int(style_id),)).fetchone()
            if r:
                ref_style = json.loads(r["profile"] or "{}")
        except Exception:
            ref_style = {}

    work = config.WORK_DIR / "strict_judge"
    work.mkdir(parents=True, exist_ok=True)

    mejor = None
    scores_seen: list[float] = []
    for rnd in range(1, MAX_ROUNDS + 1):
        progress((rnd - 1) / MAX_ROUNDS, f"Juez estricto evaluando (ronda {rnd}/{MAX_ROUNDS})")

        # 1. Métricas de estilo
        metrics = sj.edit_metrics(tl)
        sim = sj.similarity(targets, metrics)

        # 2. Contenido (texto/recursos vs transcripción)
        content = _content_judge(tl, words, dur, by_id)

        # 3. Visual (opcional, por visión) — cada ronda para llegar al 90 %
        visual = None
        visual = _visual_judge(tl, source_path, _style_summary(ref_style), work)

        # Puntuación combinada
        scores = [sim["score"]]
        pesos = [2.0]
        if visual:
            scores.append(visual["score"])
            pesos.append(1.5)
        # contenido: cada problema resta
        content_penalty = min(20.0, len(content) * 4.0)
        score = round(max(0.0, (sum(s * p for s, p in zip(scores, pesos)) / sum(pesos)) - content_penalty), 1)

        problemas = []
        problemas.extend(sim.get("advice", [])[:4])
        for p in content:
            problemas.append({"key": p["tipo"], "why_es": p["por_que"], "param": p.get("accion", "quitar"),
                              "delta": 0, "_w": 3.0})
        if visual:
            for p in visual.get("problemas", []):
                problemas.append({"key": "visual", "why_es": p["por_que"], "param": p.get("accion", "ajustar"),
                                  "delta": 0, "_w": 2.0})

        resumen = (f"score {score}% - métricas {sim['score']}%"
                   + (f", visual {visual['score']}%" if visual else "")
                   + (f", {len(content)} problemas de contenido" if content else ""))

        aprobado = score >= STRICT_GOAL and len(content) == 0
        if mejor is None or score > mejor["score"]:
            mejor = {"ronda": rnd, "score": score, "problemas": problemas, "resumen": resumen,
                     "aprobado": aprobado}

        if aprobado:
            progress(1.0, f"Juez estricto APROBÓ (ronda {rnd}, score {score})")
            for f in work.glob("sj_*.jpg"):
                f.unlink(missing_ok=True)
            return {"aprobado": True, "ronda": rnd, "score": score,
                    "reporte": {"metrics": metrics, "sim": sim, "content": content, "visual": visual,
                                "problemas": problemas, "resumen": resumen}}

        # 4. Generar correcciones aplicables
        correcciones = _build_correcciones(content, sim, targets, metrics, st)
        if not correcciones:
            break  # nada más que el juez sepa corregir: otra ronda daría lo mismo
        progress((rnd - 0.5) / MAX_ROUNDS, f"Corrigiendo (ronda {rnd}): {resumen}")
        changed, dropped = _apply_corrections(tl, st, correcciones, words)
        if changed and replace:  # con otros parámetros se vuelven a colocar los recursos ya encontrados
            replace(st, dropped)
        scores_seen.append(score)
        if len(scores_seen) >= 3 and max(scores_seen[-2:]) <= scores_seen[-3]:
            break  # dos rondas sin mejorar: se queda con la mejor

    # agotó rondas (o dejó de mejorar): devolver lo mejor
    if mejor:
        mejor["aprobado"] = False
    final_round = mejor["ronda"] if mejor else MAX_ROUNDS
    for f in work.glob("sj_*.jpg"):
        f.unlink(missing_ok=True)
    return {"aprobado": False, "ronda": final_round, "score": (mejor or {}).get("score", 0.0),
            "reporte": (mejor or {})}


def _build_correcciones(content: list[dict], sim: dict, targets: dict, metrics: dict, st: dict) -> list[dict]:
    """Convierte problemas del juez en operaciones aplicables al timeline."""
    corr: list[dict] = []

    for p in content:
        accion = p.get("accion", "quitar")
        if accion == "quitar":
            corr.append({"accion": "quitar", "id": p["id"]})
        elif accion == "mover":
            c = {"accion": "mover", "id": p["id"]}
            c.update(p.get("nuevo", {}))
            corr.append(c)
        elif accion == "acortar":
            corr.append({"accion": "acortar", "id": p["id"]})
        elif accion == "revisar":
            corr.append({"accion": "quitar", "id": p["id"]})

    # correcciones de parámetros de estilo según el juez de métricas
    alias = {"broll_per_min": "visual_per_min", "broll_duration": "visual_per_min", "zoom_every": "zoom_per_min",
             "callouts_per_min": "labels_per_min"}
    for adv in sim.get("advice", [])[:3]:
        key = alias.get(adv.get("param"), adv.get("param"))
        action = adv.get("action")
        if key == "labels_per_min":
            v = float(st.get("labels_per_min", 1.0))
            v = max(0.0, min(4.0, v * (1.3 if action == "more" else 0.75) + (0.3 if action == "more" else 0)))
            corr.append({"accion": "ajustar_parametro", "key": "labels_per_min", "valor": v})
        elif key == "visual_per_min":
            v = float(st.get("visual_per_min", 4.0))
            v = min(16.0, max(1.0, v * (1.3 if action == "more" else 0.75)))
            corr.append({"accion": "ajustar_parametro", "key": "visual_per_min", "valor": v})
        elif key == "callouts_per_min":
            v = float(st.get("callouts_per_min", 4.0))
            v = max(0.0, min(12.0, v * (1.3 if action == "more" else 0.75)))
            corr.append({"accion": "ajustar_parametro", "key": "callouts_per_min", "valor": v})
        elif key == "zoom_per_min":
            v = float(st.get("zoom_per_min", 1.5))
            v = max(0.0, min(6.0, v * (1.3 if action == "more" else 0.75)))
            corr.append({"accion": "ajustar_parametro", "key": "zoom_per_min", "valor": v})

    return corr
