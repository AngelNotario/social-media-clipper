"""IA de miniaturas: un equipo de pasos especializados, no un solo modelo.

1. Lee de qué va el video (lo que entendió y verificó la auto-edición, o lo deduce ahora).
2. Investiga en internet: miniaturas de los videos del tema con más vistas este mes y recursos (logos, portadas).
3. Elige tus mejores caras: muestrea fotogramas, detecta rostro, nitidez y tamaño; la IA de visión puntúa la expresión.
4. Recorta a la persona (rembg) o, si no se puede, usa la toma como tarjeta.
5. Diseñador (modelo «Miniaturas»): con tus instrucciones, tus ejemplos y las referencias de hoy propone N conceptos.
6. Compone cada concepto (Pillow) y un juez con visión los califica como se ven en el feed; si ninguno convence,
   el diseñador corrige una vez con la crítica.
"""
from __future__ import annotations

import base64
import io
import json
import subprocess
import uuid
from pathlib import Path

from PIL import Image

from .. import config, db, research
from ..ai import llm
from . import compose, store

WORK = config.DATA_DIR / "thumbs"


def _b64(img: Image.Image, w: int = 512, fmt: str = "JPEG") -> str:
    im = img.convert("RGB")
    if im.width > w:
        im = im.resize((w, int(w * im.height / im.width)), Image.LANCZOS)
    buf = io.BytesIO()
    im.save(buf, fmt, quality=85)
    return base64.b64encode(buf.getvalue()).decode()


def vision_model() -> str:
    return db.get_setting("vision_model") or "qwen3.6:35b-a3b"


# ---------------- 1. fuente ----------------

def resolve(kind: str, sid: int) -> dict:
    """→ {path, title, text, duration, understood, research, chroma_bg (ruta del video de fondo o None)}"""
    if kind in ("clip_render", "studio_render"):
        from ..publish import sources
        src = sources.resolve(kind, sid)
        src["chroma_bg"] = None
        if kind == "studio_render":
            with db.get_conn() as conn:
                r = conn.execute("SELECT p.timeline FROM studio_renders r JOIN studio_projects p ON p.id = r.project_id "
                                 "WHERE r.id = ?", (sid,)).fetchone()
            ch = (json.loads(r["timeline"]) if r and r["timeline"] else {}).get("chroma") or {}
            if ch.get("enabled") and (ch.get("bg") or {}).get("video_id"):
                with db.get_conn() as conn:
                    v = conn.execute("SELECT stored_path FROM videos WHERE id = ?", (ch["bg"]["video_id"],)).fetchone()
                if v and Path(v["stored_path"]).exists():
                    src["chroma_bg"] = v["stored_path"]
        return src
    if kind == "project":
        with db.get_conn() as conn:
            r = conn.execute("SELECT * FROM studio_projects WHERE id = ?", (sid,)).fetchone()
        if not r:
            raise store.ThumbError("Proyecto no encontrado.")
        sm = json.loads(r["source_meta"] or "{}")
        tl = json.loads(r["timeline"] or "{}")
        words = tl.get("words") or []
        ch = tl.get("chroma") or {}
        bgp = None
        if ch.get("enabled") and (ch.get("bg") or {}).get("video_id"):
            with db.get_conn() as conn:
                v = conn.execute("SELECT stored_path FROM videos WHERE id = ?", (ch["bg"]["video_id"],)).fetchone()
            bgp = v["stored_path"] if v and Path(v["stored_path"]).exists() else None
        return {"path": r["source_path"], "title": r["name"], "text": " ".join(w["word"] for w in words)[:6000],
                "duration": sm.get("duration") or 0, "understood": sm.get("understood"), "research": sm.get("research"),
                "chroma_bg": bgp, "segments": [(s["src_in"], s["src_out"]) for s in tl.get("tracks", {}).get("video", [])]}
    if kind == "video":
        with db.get_conn() as conn:
            v = conn.execute("SELECT * FROM videos WHERE id = ?", (sid,)).fetchone()
            t = conn.execute("SELECT segments FROM transcripts WHERE video_id = ?", (sid,)).fetchone()
        if not v:
            raise store.ThumbError("Video no encontrado.")
        segs = json.loads(t["segments"]) if t else []
        return {"path": v["stored_path"], "title": v["title"], "text": " ".join(s.get("text", "") for s in segs)[:6000],
                "duration": v["duration"] or 0, "understood": None, "research": None, "chroma_bg": None}
    raise store.ThumbError("Tipo de fuente no soportado.")


# ---------------- 3. caras ----------------

def grab(path: str, t: float, out: Path, width: int | None = None) -> Path | None:
    vf = ["-vf", f"scale={width}:-2"] if width else []
    r = subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{max(0.0, t):.2f}", "-i", str(path), "-frames:v", "1",
                        *vf, "-q:v", "2", str(out)], capture_output=True, timeout=60)
    return out if r.returncode == 0 and out.exists() else None


def candidate_frames(src: dict, work: Path, n_sample: int = 32, keep: int = 6, progress=lambda p, s: None) -> list[dict]:
    """Fotogramas con tu cara bien visible, nítida y grande; repartidos por el video."""
    import cv2
    import numpy as np
    from ..editor import crop
    det = crop._detector()
    dur = float(src.get("duration") or 0) or 10.0
    spans = src.get("segments") or [(dur * 0.03, dur * 0.97)]
    total = sum(b - a for a, b in spans) or dur
    times = []
    for k in range(n_sample):  # repartidos sobre lo que queda en el timeline (no en lo cortado)
        x = total * (k + 0.5) / n_sample
        for a, b in spans:
            if x <= b - a:
                times.append(a + x)
                break
            x -= b - a
    cands = []
    for i, t in enumerate(times):
        progress(i / len(times), "Buscando tus mejores caras")
        f = grab(src["path"], t, work / f"s_{i}.jpg", 640)
        if not f:
            continue
        img = cv2.imread(str(f))
        if img is None:
            continue
        h, w = img.shape[:2]
        faces = det(img) if det else []
        if not faces:
            continue
        x, y, fw, fh, sc = max(faces, key=lambda z: z[2] * z[3])
        roi = cv2.cvtColor(img[max(0, int(y)):int(y + fh), max(0, int(x)):int(x + fw)], cv2.COLOR_BGR2GRAY)
        sharp = float(cv2.Laplacian(roi, cv2.CV_64F).var()) if roi.size else 0.0
        area = fw * fh / (w * h)
        score = float(np.sqrt(area) * min(sharp, 400) / 400 * (0.6 + 0.4 * sc))
        cands.append({"t": round(t, 2), "face": [x / w, y / h, fw / w, fh / h], "score": score, "sample": str(f)})
    cands.sort(key=lambda c: c["score"], reverse=True)
    picked = []
    for c in cands:
        if all(abs(c["t"] - p["t"]) > dur * 0.06 for p in picked):
            picked.append(c)
        if len(picked) >= keep:
            break
    return picked


FACES_SCHEMA = {"type": "object", "properties": {"frames": {"type": "array", "items": {"type": "object", "properties": {
    "i": {"type": "integer"}, "expresion": {"type": "string"}, "intensidad": {"type": "number"},
    "apta": {"type": "number"}}, "required": ["i", "expresion", "intensidad", "apta"]}}}, "required": ["frames"]}


def rate_faces(cands: list[dict]) -> list[dict]:
    """La IA de visión puntúa la expresión de cada cara (las miniaturas viven de la emoción)."""
    if not cands:
        return cands
    imgs = [_b64(Image.open(c["sample"]), 384) for c in cands]
    try:
        r = llm.chat_json(db.get_setting("ollama_url"), vision_model(),
                          "Eres director de arte de miniaturas de YouTube. Te doy fotogramas numerados desde 0. Para cada uno: "
                          "'expresion' (2-4 palabras), 'intensidad' 0-10 (qué tan expresiva/emotiva es la cara) y 'apta' 0-10 "
                          "(ojos abiertos, cara clara, sin desenfoque, buena para miniatura). Responde solo JSON.",
                          f"{len(imgs)} fotogramas, en orden.", FACES_SCHEMA, images=imgs, max_tokens=600)
        by = {f["i"]: f for f in r.get("frames") or []}
        for i, c in enumerate(cands):
            f = by.get(i) or {}
            c.update(expresion=f.get("expresion", ""), intensidad=float(f.get("intensidad") or 5),
                     apta=float(f.get("apta") or 5))
            c["score"] = c["score"] * 0.4 + (c["intensidad"] * 0.6 + c["apta"] * 0.4) / 10 * 0.6
    except llm.LLMError:
        for c in cands:
            c.setdefault("expresion", ""), c.setdefault("intensidad", 5), c.setdefault("apta", 5)
    return sorted(cands, key=lambda c: c["score"], reverse=True)


# ---------------- 4. recorte ----------------

def subject_cutout(src_path: str, cand: dict, work: Path, idx: int) -> Image.Image | None:
    full = grab(src_path, cand["t"], work / f"f_{idx}.png")
    if not full:
        return None
    im = Image.open(full).convert("RGB")
    w, h = im.size
    fx, fy, fw, fh = cand["face"]
    # región de la persona: alrededor de la cara (cabeza + hombros/torso)
    cx = (fx + fw / 2) * w
    x0, x1 = max(0, cx - fw * w * 2.1), min(w, cx + fw * w * 2.1)
    y0, y1 = max(0, fy * h - fh * h * 0.9), min(h, fy * h + fh * h * 4.2)
    region = im.crop((int(x0), int(y0), int(x1), int(y1)))
    try:
        from rembg import new_session, remove
        global _SESSION
        if "_SESSION" not in globals():
            _SESSION = new_session("u2net_human_seg")
        cut = remove(region, session=_SESSION, post_process_mask=True)
        a = cut.getchannel("A")
        if a.getbbox() and sum(a.histogram()[200:]) > region.width * region.height * 0.08:
            out = cut.crop(a.getbbox())
            out.save(work / f"cut_{idx}.png")
            return out
    except Exception:  # noqa: BLE001 — sin rembg o falló: tarjeta
        pass
    out = compose.card(region)
    out.save(work / f"cut_{idx}.png")
    return out


# ---------------- 2. referencias y recursos ----------------

def load_image(ref: str, work: Path, name: str) -> Image.Image | None:
    """Ruta subida, URL de imagen o enlace de YouTube (se toma su miniatura)."""
    try:
        if ref.startswith("upload:"):
            p = store.UPLOADS / ref[7:]
            return Image.open(p).convert("RGBA") if p.exists() else None
        vid = research.youtube_id(ref)
        urls = [f"https://i.ytimg.com/vi/{vid}/maxresdefault.jpg", f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg"] if vid else [ref]
        for u in urls:
            p = research.download(u, work / f"{name}.img")
            if p:
                im = Image.open(p)
                if im.width > 200:
                    return im.convert("RGBA")
    except Exception:  # noqa: BLE001
        return None
    return None


REF_SCHEMA = {"type": "object", "properties": {
    "notas": {"type": "array", "items": {"type": "string"}},
    "paleta": {"type": "array", "items": {"type": "string"}},
    "texto": {"type": "string"}, "composicion": {"type": "string"}, "por_que_funcionan": {"type": "string"}},
    "required": ["notas", "paleta", "texto", "composicion", "por_que_funcionan"]}


def study_references(refs: list[Image.Image], user_given: bool) -> dict | None:
    if not refs:
        return None
    try:
        return llm.chat_json(db.get_setting("ollama_url"), vision_model(),
                             "Eres director de arte. Analiza estas miniaturas de YouTube " +
                             ("que el creador te da como EJEMPLO de lo que quiere" if user_given else
                              "de los videos del tema con más vistas este mes") +
                             ". Responde solo JSON: 'notas' (5-8 rasgos concretos y copiables: dónde va la cara, tamaño del texto, "
                             "colores, elementos, flechas/círculos, emoción), 'paleta' (colores #RRGGBB dominantes), 'texto' (cómo "
                             "usan el texto: cuántas palabras, estilo), 'composicion', 'por_que_funcionan'.",
                             f"{len(refs)} miniaturas.", REF_SCHEMA, images=[_b64(r, 512) for r in refs[:6]], max_tokens=700)
    except llm.LLMError:
        return None


# ---------------- 5. diseñador ----------------

CONCEPT = {"type": "object", "properties": {
    "text": {"type": "string"}, "highlight_word": {"type": "string"}, "text_color": {"type": "string"},
    "accent_color": {"type": "string"}, "highlight_box": {"type": "boolean"},
    "layout": {"type": "string", "enum": ["left", "right", "center"]}, "face": {"type": "integer"},
    "background": {"type": "string"}, "bg_color_1": {"type": "string"}, "bg_color_2": {"type": "string"},
    "elements": {"type": "array", "items": {"type": "object", "properties": {
        "asset": {"type": "string"}, "position": {"type": "string", "enum": list(compose.POS)},
        "size": {"type": "string", "enum": list(compose.SIZES)}}, "required": ["asset", "position", "size"]}},
    "arrow": {"type": "boolean"}, "idea": {"type": "string"}},
    "required": ["text", "highlight_word", "text_color", "accent_color", "layout", "face", "background", "elements", "idea"]}
DESIGN_SCHEMA = {"type": "object", "properties": {
    "asset_queries": {"type": "array", "items": {"type": "string"}},
    "concepts": {"type": "array", "items": CONCEPT}}, "required": ["concepts"]}

DESIGN_SYSTEM = """Eres diseñador profesional de miniaturas virales de YouTube (CTR alto) para creadores en español.
Reglas de oro: 2-4 palabras GIGANTES (nunca repetir el título completo, complementarlo con curiosidad o emoción),
una palabra resaltada en color de acento, cara expresiva grande a un lado y texto al otro, alto contraste, máximo
2-3 elementos (logos/objetos reconocibles del tema), fondo con color y profundidad, legible en pequeño (móvil).
Sé FIEL al tema verificado: nada de clickbait falso ni de afirmar lo marcado como dudoso.
Responde solo JSON con "concepts" (los que se piden, distintos entre sí: cambia texto, ángulo emocional, colores,
lado de la cara y fondo) y, si ayuda, "asset_queries" (2-3 búsquedas de imágenes en internet de logos/objetos
reconocibles del tema, p. ej. "GTA 6 logo png").
Campos: text, highlight_word (una palabra de text), text_color y accent_color (#RRGGBB), highlight_box (palabra en caja),
layout (left/right/center = dónde va la cara), face (índice de la cara elegida), background ("frame" = fotograma del video,
"gameplay" = escena del video de fondo si existe, "asset:ID" = un recurso, "gradient"), bg_color_1/bg_color_2 (para gradient),
elements [{asset: ID, position, size}], arrow (flecha hacia la cara), idea (por qué funciona, 1 frase)."""


def designer(ctx: dict, n: int, critique: str | None = None) -> dict:
    lines = [f"Tema verificado: {ctx['tema']}", f"Postura del creador: {ctx.get('postura', '')}",
             f"Título del video: {ctx.get('title', '')}"]
    if ctx.get("facts"):
        lines.append(ctx["facts"])
    if ctx.get("instructions"):
        lines.append(f"INSTRUCCIONES DEL CREADOR (mandan): {ctx['instructions']}")
    if ctx.get("fixed_text"):
        lines.append(f"Texto que el creador quiere en la miniatura: «{ctx['fixed_text']}» (úsalo tal cual).")
    if ctx.get("ref_user"):
        lines.append("EJEMPLOS DEL CREADOR (imítalos en estilo): " + json.dumps(ctx["ref_user"], ensure_ascii=False))
    if ctx.get("ref_trend"):
        lines.append("Miniaturas del tema con más vistas este mes: " + json.dumps(ctx["ref_trend"], ensure_ascii=False))
    lines.append("CARAS disponibles: " + "; ".join(f"{i}: {c.get('expresion') or 'cara'} (intensidad {c.get('intensidad', 5):.0f})"
                                                  for i, c in enumerate(ctx["faces"])))
    lines.append("RECURSOS disponibles: " + ("; ".join(f"{k}: {v}" for k, v in ctx["assets_desc"].items()) or "(ninguno)"))
    if ctx.get("has_gameplay"):
        lines.append("Hay escena de gameplay disponible como fondo (background: \"gameplay\").")
    if critique:
        lines.append(f"CRÍTICA de la ronda anterior (corrígela): {critique}")
    lines.append(f"Propón {n} conceptos.")
    model = db.role_model("thumbnail")
    return llm.chat_json(db.get_setting("ollama_url"), model, DESIGN_SYSTEM, "\n".join(lines), DESIGN_SCHEMA,
                         num_ctx=12288, max_tokens=1600)


JUDGE_SCHEMA = {"type": "object", "properties": {"scores": {"type": "array", "items": {"type": "object", "properties": {
    "i": {"type": "integer"}, "legibilidad": {"type": "number"}, "curiosidad": {"type": "number"},
    "contraste": {"type": "number"}, "emocion": {"type": "number"}, "fiel_al_tema": {"type": "number"},
    "critica": {"type": "string"}}, "required": ["i", "legibilidad", "curiosidad", "contraste", "emocion", "fiel_al_tema",
                                                  "critica"]}}}, "required": ["scores"]}


def judge(paths: list[Path], tema: str) -> list[dict]:
    """Juez con visión: califica cada miniatura vista en tamaño de feed (pequeña), 0-10 por criterio."""
    try:
        r = llm.chat_json(db.get_setting("ollama_url"), vision_model(),
                          "Eres analista de CTR de YouTube. Te doy miniaturas numeradas desde 0 en el tamaño en que se ven en el "
                          "celular. Califica 0-10: legibilidad (¿se lee el texto en pequeño?), curiosidad (¿dan ganas de hacer "
                          "clic?), contraste, emocion (cara/expresión), fiel_al_tema. 'critica': qué cambiar, concreto. Solo JSON.",
                          f"Tema del video: {tema}", JUDGE_SCHEMA, images=[_b64(compose.tiny(p, 420), 420) for p in paths],
                          max_tokens=700)
        out = []
        for s in r.get("scores") or []:
            vals = [float(s.get(k) or 0) for k in ("legibilidad", "curiosidad", "contraste", "emocion", "fiel_al_tema")]
            out.append({"i": int(s.get("i", -1)), "score": round(sum(vals) / 5, 2), "critique": s.get("critica", ""),
                        "detail": dict(zip(("legibilidad", "curiosidad", "contraste", "emocion", "fiel_al_tema"), vals))})
        return out
    except llm.LLMError:
        return []


# ---------------- 6. trabajo completo ----------------

def _topic(src: dict) -> dict:
    und = src.get("understood") or {}
    if und.get("tema"):
        return {"tema": und["tema"], "postura": und.get("postura", ""), "consultas": und.get("consultas") or [und["tema"]]}
    from ..publish.metadata import QUERY_SCHEMA
    try:
        q = llm.chat_json(db.get_setting("ollama_url"), db.role_model("writer"),
                          'Lee lo que se dice en un video y responde solo JSON: "tema" (una frase precisa) y "consultas" '
                          '(2-3 búsquedas cortas sobre el tema).',
                          f"Título: {src.get('title')}\nLo que se dice: {(src.get('text') or '')[:3500]}", QUERY_SCHEMA,
                          max_tokens=200)
        return {"tema": q.get("tema") or src.get("title", ""), "postura": "", "consultas": q.get("consultas") or []}
    except llm.LLMError:
        return {"tema": src.get("title", ""), "postura": "", "consultas": [src.get("title", "")]}


def run_generate(job: dict, progress) -> dict:
    prm = job["params"] or {}
    kind, sid = prm["source_kind"], int(prm["source_id"])
    n = max(1, min(6, int(prm.get("n") or 3)))
    use_web = bool(prm.get("use_web", True)) and research.enabled()
    batch = uuid.uuid4().hex[:10]
    work = WORK / batch
    work.mkdir(parents=True, exist_ok=True)
    progress(0.02, "Leyendo de qué trata tu video")
    src = resolve(kind, sid)
    topic = _topic(src)
    ctx = {"tema": topic["tema"], "postura": topic["postura"], "title": src.get("title"),
           "instructions": (prm.get("instructions") or "").strip()[:1200], "fixed_text": (prm.get("text") or "").strip()[:60],
           "facts": research.as_prompt(src.get("research")), "assets_desc": {}}
    trend, ref_trend_imgs = [], []
    if use_web:
        progress(0.08, "Investigando en internet las miniaturas que funcionan hoy")
        try:
            trend = research.trending_videos(topic["consultas"][0] if topic["consultas"] else topic["tema"], 6)
        except research.ResearchError:
            trend = []
        for i, t in enumerate(trend[:4]):
            im = load_image(t["thumbnail"], work, f"trend_{i}") if t.get("thumbnail") else None
            if im is not None:
                ref_trend_imgs.append(im)
        if not (src.get("research") or {}).get("sources"):
            try:
                fc = research.fact_check(topic["tema"], [(src.get("text") or "")[:400]], topic["consultas"])
                ctx["facts"] = research.as_prompt(fc)
            except Exception:  # noqa: BLE001
                pass
    user_refs = [im for i, r in enumerate(prm.get("references") or []) if (im := load_image(str(r), work, f"ref_{i}"))]
    progress(0.2, "Estudiando ejemplos y referencias")
    ctx["ref_user"] = study_references(user_refs, True) if user_refs else None
    ctx["ref_trend"] = study_references(ref_trend_imgs, False) if ref_trend_imgs else None

    progress(0.3, "Buscando tus mejores caras")
    faces = rate_faces(candidate_frames(src, work, progress=lambda p, s: progress(0.3 + 0.15 * p, s)))
    ctx["faces"] = faces[:5]
    assets: dict[str, Image.Image] = {}
    for i, r in enumerate(prm.get("assets") or []):  # recursos que tú das: mandan
        im = load_image(str(r), work, f"asset_{i}")
        if im is not None:
            assets[f"u{i}"] = im
            ctx["assets_desc"][f"u{i}"] = f"recurso del creador {i + 1}"
    gameplay = None
    if src.get("chroma_bg"):
        gp = grab(src["chroma_bg"], 90.0, work / "gameplay.jpg")
        gameplay = Image.open(gp).convert("RGB") if gp else None
    ctx["has_gameplay"] = gameplay is not None

    progress(0.5, "El diseñador está pensando las miniaturas")
    design = designer(ctx, n)
    if use_web and design.get("asset_queries"):  # recursos reconocibles del tema, actuales
        for qi, q in enumerate(design["asset_queries"][:3]):
            progress(0.55 + qi * 0.03, f"Buscando en internet: {q[:40]}")
            try:
                found = research.images(q, 6)
            except research.ResearchError:
                continue
            for j, f in enumerate(sorted(found, key=lambda x: -(research._int(x.get("width")) * research._int(x.get("height"))))[:3]):
                p = research.download(f["image"], work / f"web_{qi}_{j}.img")
                try:
                    im = Image.open(p).convert("RGBA") if p else None
                except Exception:  # noqa: BLE001
                    im = None
                if im is not None and im.width >= 160:
                    key = f"w{qi}"
                    assets[key] = im
                    ctx["assets_desc"][key] = f"{q} (de {f.get('source') or f.get('url', '')[:40]})"
                    store.add_asset_source(batch, key, f)
                    break
        design = designer(ctx, n)  # ahora sí con los recursos encontrados
    for key, im in assets.items():  # para poder re-editar luego sin volver a buscar
        try:
            im.save(work / f"a_{key}.png")
        except Exception:  # noqa: BLE001
            pass
    concepts = (design.get("concepts") or [])[:n]
    if not concepts:
        raise store.ThumbError("El diseñador no propuso conceptos. Revisa que Ollama esté abierto.")

    def render_all(concepts: list[dict], tag: str) -> list[tuple[dict, Path]]:
        outs = []
        for i, c in enumerate(concepts):
            progress(0.65 + 0.2 * i / len(concepts), f"Componiendo la miniatura {i + 1}/{len(concepts)}")
            fi = int(c.get("face") or 0)
            cand = faces[fi] if 0 <= fi < len(faces) else (faces[0] if faces else None)
            sub = subject_cutout(src["path"], cand, work, fi) if cand else None
            bg = gameplay if (c.get("background") == "gameplay" and gameplay is not None) else None
            if bg is None and cand:
                fr = work / f"f_{fi}.png"
                bg = Image.open(fr).convert("RGB") if fr.exists() else None
                if c.get("background") == "gameplay":
                    c["background"] = "frame"
            outs.append((c, compose.compose(c, sub, bg, assets, work / f"{tag}_{i}.jpg")))
        return outs

    made = render_all(concepts, "v")
    progress(0.86, "El juez está calificando cómo se ven en el celular")
    scores = judge([p for _, p in made], topic["tema"])
    best = max((s["score"] for s in scores), default=0)
    if scores and best < 6.5 and prm.get("refine", True):  # nadie convence: una ronda de corrección
        progress(0.9, "Corrigiendo con la crítica del juez")
        crit = " | ".join(f"#{s['i']}: {s['critique']}" for s in scores)
        try:
            d2 = designer(ctx, n, critique=crit)
            more = render_all((d2.get("concepts") or [])[:n], "r")
            s2 = judge([p for _, p in more], topic["tema"])
            off = len(made)
            made += more
            scores += [{**s, "i": s["i"] + off} for s in s2]
        except llm.LLMError:
            pass
    by = {s["i"]: s for s in scores}
    ids = []
    for i, (c, p) in enumerate(made):
        s = by.get(i, {})
        ids.append(store.save(batch, kind, sid, c, p, s.get("score"), s.get("critique"), s.get("detail"),
                              {"tema": topic["tema"], "trend": trend[:4], "instructions": ctx["instructions"]}))
    for f in work.glob("s_*.jpg"):
        f.unlink(missing_ok=True)
    return {"batch": batch, "ids": ids, "best": max(scores, key=lambda s: s["score"])["score"] if scores else None,
            "trend": [t["title"] for t in trend[:4]], "web_assets": [k for k in assets if k.startswith("w")]}


def recompose(thumb: dict, patch: dict) -> Path:
    """Re-render rápido con tu texto/colores/lado (sin IA)."""
    batch_dir = WORK / thumb["batch"]
    c = {**thumb["concept"], **{k: v for k, v in patch.items() if k in ("text", "highlight_word", "text_color",
                                                                        "accent_color", "highlight_box", "layout", "arrow")}}
    fi = int(c.get("face") or 0)
    fr = batch_dir / f"f_{fi}.png"
    sub = Image.open(batch_dir / f"cut_{fi}.png") if (batch_dir / f"cut_{fi}.png").exists() else None
    bg = Image.open(fr).convert("RGB") if fr.exists() else None
    if c.get("background") == "gameplay" and (batch_dir / "gameplay.jpg").exists():
        bg = Image.open(batch_dir / "gameplay.jpg").convert("RGB")
    assets = {}
    for f in batch_dir.glob("a_*.png"):
        try:
            assets[f.stem[2:]] = Image.open(f).convert("RGBA")
        except Exception:  # noqa: BLE001
            pass
    out = batch_dir / f"e_{thumb['id']}_{uuid.uuid4().hex[:4]}.jpg"
    compose.compose(c, sub, bg, assets, out)
    return out
