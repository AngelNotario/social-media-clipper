"""Pantalla verde (chroma key): quita el fondo de color y pone detrás un gameplay, video, imagen o color.

En el timeline: tl["chroma"] = {
  enabled, color "#RRGGBB" (o "auto"), similarity 0.05-0.5, blend 0-0.3, despill,
  bg: {kind: video|asset|color|none, video_id, asset_id, color, start (s), volume (0-1)},
  subject: {scale 0.3-1.0, x 0-1, y 0-1}   # tamaño y posición de la persona sobre el fondo
}
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from .. import db

DEFAULT = {"enabled": False, "color": "#00FF00", "similarity": 0.14, "blend": 0.08, "despill": True,
           "bg": {"kind": "none", "video_id": None, "asset_id": None, "color": "#101018", "start": 0.0, "volume": 0.0},
           "subject": {"scale": 1.0, "x": 0.5, "y": 1.0}}


def _clamp(v, a, b, d):
    try:
        return max(a, min(b, float(v)))
    except (TypeError, ValueError):
        return d


def normalize(ch: dict | None) -> dict:
    ch = ch or {}
    bg = {**DEFAULT["bg"], **(ch.get("bg") or {})}
    sub = {**DEFAULT["subject"], **(ch.get("subject") or {})}
    color = str(ch.get("color") or DEFAULT["color"])
    if not (color.startswith("#") and len(color) == 7):
        color = DEFAULT["color"]
    return {"enabled": bool(ch.get("enabled")), "color": color.upper(),
            "similarity": _clamp(ch.get("similarity"), 0.02, 0.6, DEFAULT["similarity"]),
            "blend": _clamp(ch.get("blend"), 0.0, 0.4, DEFAULT["blend"]),
            "despill": bool(ch.get("despill", True)),
            "bg": {"kind": bg["kind"] if bg["kind"] in ("video", "asset", "color", "none") else "none",
                   "video_id": bg.get("video_id"), "asset_id": bg.get("asset_id"),
                   "color": bg.get("color") or "#101018", "start": _clamp(bg.get("start"), 0, 1e7, 0.0),
                   "volume": _clamp(bg.get("volume"), 0, 1, 0.0)},
            "subject": {"scale": _clamp(sub.get("scale"), 0.25, 1.0, 1.0), "x": _clamp(sub.get("x"), 0, 1, 0.5),
                        "y": _clamp(sub.get("y"), 0, 1, 1.0)},
            "protect": _protect(ch.get("protect")),
            "bg_schedule": _schedule(ch.get("bg_schedule"))}


def _schedule(items) -> list[dict]:
    """Fondo por tramos (edición generativa): se conserva tal cual, solo se descartan entradas inválidas."""
    out = []
    for e in items or []:
        if not isinstance(e, dict):
            continue
        try:
            a, b = float(e["start"]), float(e["end"])
        except (KeyError, TypeError, ValueError):
            continue
        if b - a >= 0.3 and (e.get("asset_id") or e.get("video_id") or e.get("kind") == "text"):
            out.append({**e, "start": round(a, 3), "end": round(b, 3)})
    return sorted(out, key=lambda e: e["start"])[:400]


def _protect(rects) -> list[dict]:
    """Zonas de la toma que nunca se recortan (p. ej. el recuadro de tu cámara en OBS): así un objeto verde
    dentro de tu cuarto no se vuelve transparente. Fracciones del cuadro original."""
    out = []
    for r in rects or []:
        try:
            x, y, w, h = (max(0.0, min(1.0, float(r[k]))) for k in ("x", "y", "w", "h"))
        except (KeyError, TypeError, ValueError):
            continue
        if w > 0.02 and h > 0.02:
            out.append({"x": round(x, 4), "y": round(min(y, 1 - h), 4), "w": round(min(w, 1 - x), 4), "h": round(h, 4)})
    return out[:4]


def active(tl: dict) -> dict | None:
    ch = normalize(tl.get("chroma"))
    return ch if ch["enabled"] else None


def despill_type(hex_color: str) -> str | None:
    r, g, b = (int(hex_color[i:i + 2], 16) for i in (1, 3, 5))
    if g > r and g > b:
        return "green"
    if b > r and b > g:
        return "blue"
    return None


def bg_source(ch: dict) -> tuple[str | None, bool]:
    """→ (ruta, es_imagen) del fondo."""
    bg = ch["bg"]
    if bg["kind"] == "video" and bg.get("video_id"):
        with db.get_conn() as conn:
            r = conn.execute("SELECT stored_path FROM videos WHERE id = ?", (bg["video_id"],)).fetchone()
        p = r["stored_path"] if r else None
        if p and "#" in p and not Path(p).exists():
            p = p.split("#")[0]
        return (p if p and Path(p).exists() else None), False
    if bg["kind"] == "asset" and bg.get("asset_id"):
        from . import assets
        a = next((x for x in assets.list_assets() if x["id"] == bg["asset_id"]), None)
        if a and Path(a["path"]).exists():
            return a["path"], a.get("media_type") == "image"
    return None, False


def detect_color(path: str | Path, at: float = 1.0) -> dict:
    """Mide el color real de tu pantalla verde mirando el borde del cuadro (donde casi nunca estás tú).
    Devuelve {color, similarity} ajustados a tu iluminación."""
    import numpy as np
    w, h = 96, 96
    try:
        raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{max(at, 0):.2f}", "-i", str(path), "-frames:v", "1",
                              "-vf", f"scale={w}:{h}", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                             capture_output=True, timeout=60).stdout
    except (OSError, subprocess.TimeoutExpired):
        raw = b""
    if len(raw) < w * h * 3:
        return {"color": DEFAULT["color"], "similarity": DEFAULT["similarity"], "detected": False}
    img = np.frombuffer(raw[: w * h * 3], np.uint8).reshape(h, w, 3).astype(np.int32)
    m = 10
    border = np.concatenate([img[:m].reshape(-1, 3), img[-m:].reshape(-1, 3),
                             img[:, :m].reshape(-1, 3), img[:, -m:].reshape(-1, 3)])
    r, g, b = border[:, 0], border[:, 1], border[:, 2]
    green = border[(g > r + 25) & (g > b + 25)]
    blue = border[(b > r + 25) & (b > g + 15)]
    pick = green if len(green) >= len(blue) else blue
    if len(pick) < len(border) * 0.15:
        return {"color": DEFAULT["color"], "similarity": DEFAULT["similarity"], "detected": False}
    med = np.median(pick, axis=0)
    spread = float(np.mean(np.abs(pick - med))) / 255  # iluminación desigual → más tolerancia
    sim = round(min(0.32, max(0.1, 0.1 + spread * 1.6)), 3)
    return {"color": "#%02X%02X%02X" % tuple(int(x) for x in med), "similarity": sim, "detected": True,
            "coverage": round(len(pick) / len(border), 2)}


def pre_key(ch: dict, i: int, sw: int, sh: int) -> str:
    """Recorte por tramo, sobre la toma original (antes de zooms y encuadres): así las zonas protegidas siguen
    a la imagen aunque haya zoom. Devuelve filtros para encadenar justo después de la entrada del tramo;
    la salida lleva canal alfa (yuva420p)."""
    key = ch["color"].lstrip("#")
    sp = despill_type(ch["color"]) if ch["despill"] else None
    rects = []
    for r in ch.get("protect") or []:
        x, y = int(r["x"] * sw) // 2 * 2, int(r["y"] * sh) // 2 * 2
        w, h = int(r["w"] * sw) // 2 * 2, int(r["h"] * sh) // 2 * 2
        if w >= 8 and h >= 8:
            rects.append((x, y, min(w, sw - x), min(h, sh - y)))
    km = f"{key}:{ch['similarity']:.3f}:{ch['blend']:.3f}"
    if not rects and not sp:  # caso simple: el recorte tal cual
        return f",format=yuv420p,chromakey=0x{km},format=yuva420p"
    labs = ([f"[ko{i}]"] if rects else []) + [f"[km{i}]"] + ([f"[kd{i}]"] if sp else [])
    boxes = "".join(f",drawbox=x={x}:y={y}:w={w}:h={h}:color=white:t=fill" for x, y, w, h in rects)
    out = f",format=yuv420p,split={len(labs)}" + "".join(labs) + ";"
    out += f"[km{i}]chromakey=0x{km},format=yuva420p,alphaextract,format=gray{boxes}[kmask{i}];"
    base = f"[ko{i}]"
    if sp:
        out += f"[kd{i}]despill=type={sp}:mix=0.5:expand=0,format=yuv420p[kdes{i}];"
        src = f"[kdes{i}]"
        if rects:  # el despill no debe tocar lo protegido: se repone el original encima
            out += f"[ko{i}]split={len(rects)}" + "".join(f"[kp{i}_{j}]" for j in range(len(rects))) + ";"
            for j, (x, y, w, h) in enumerate(rects):
                out += f"[kp{i}_{j}]crop={w}:{h}:{x}:{y}[kc{i}_{j}];{src}[kc{i}_{j}]overlay={x}:{y}[kf{i}_{j}];"
                src = f"[kf{i}_{j}]"
        base = src
    return out + f"{base}[kmask{i}]alphamerge,format=yuva420p"


def sample_times(tl: dict, n: int = 10) -> list[float]:
    """Momentos de la toma original repartidos por todo lo que usa la edición."""
    segs = sorted(tl["tracks"]["video"], key=lambda s: s.get("start", 0))
    if not segs:
        return [1.0]
    pick = [segs[round(j * (len(segs) - 1) / max(1, n - 1))] for j in range(min(n, len(segs)))]
    return [round((s.get("src_in", 0) + s.get("src_out", 1)) / 2, 2) for s in pick]


def detect_protect(path: str | Path, ch: dict, times: list[float]) -> list[dict]:
    """Busca el recuadro fijo de tu cámara (lo que nunca es verde en ninguna parte del video) para protegerlo."""
    import numpy as np
    w, h = 480, 270
    key = ch["color"].lstrip("#")
    kr, kg, kb = (int(key[j:j + 2], 16) for j in (0, 2, 4))
    kcb, kcr = -0.168736 * kr - 0.331264 * kg + 0.5 * kb, 0.5 * kr - 0.418688 * kg - 0.081312 * kb
    keep = None
    for t in times[:10]:
        try:
            raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{max(t, 0):.2f}", "-i", str(path), "-frames:v", "1",
                                  "-vf", f"scale={w}:{h}", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                                 capture_output=True, timeout=60).stdout
        except (OSError, subprocess.TimeoutExpired):
            continue
        if len(raw) < w * h * 3:
            continue
        im = np.frombuffer(raw[: w * h * 3], np.uint8).reshape(h, w, 3).astype(np.float32)
        r, g, b = im[..., 0], im[..., 1], im[..., 2]
        du = (-0.168736 * r - 0.331264 * g + 0.5 * b) - kcb
        dv = (0.5 * r - 0.418688 * g - 0.081312 * b) - kcr
        solid = np.sqrt((du * du + dv * dv) / 130050) > ch["similarity"] + ch["blend"]
        keep = solid if keep is None else (keep & solid)
    if keep is None or keep.mean() < 0.01:
        return []
    raw_keep = keep
    keep = _close(keep, 6)  # cierra huecos: lo verde dentro de tu cuarto no parte el recuadro en pedazos
    lab, n = _label(keep)
    boxes = []
    for k in range(1, n + 1):
        ys, xs = np.nonzero(lab == k)
        if len(ys) >= w * h * 0.003:
            boxes.append([xs.min(), ys.min(), xs.max() + 1, ys.max() + 1])
    merged = True  # pedazos pegados (algo verde dentro de la cámara puede partirla) → un solo recuadro
    while merged:
        merged = False
        for i1 in range(len(boxes)):
            for i2 in range(i1 + 1, len(boxes)):
                A, B = boxes[i1], boxes[i2]
                if A[0] <= B[2] + 6 and B[0] <= A[2] + 6 and A[1] <= B[3] + 6 and B[1] <= A[3] + 6:
                    boxes[i1] = [min(A[0], B[0]), min(A[1], B[1]), max(A[2], B[2]), max(A[3], B[3])]
                    boxes.pop(i2)
                    merged = True
                    break
            if merged:
                break
    found = []
    for x0, y0, x1, y1 in boxes:
        bw, bh = x1 - x0, y1 - y0
        area = bw * bh
        if area < w * h * 0.015 or area > w * h * 0.6:
            continue  # muy chico, o casi todo el cuadro (entonces no hay recuadro que proteger)
        if keep[y0:y1, x0:x1].mean() < 0.7:
            continue  # no es un recuadro sólido
        sub = raw_keep[y0:y1, x0:x1]  # bordes exactos con la máscara sin cerrar (filas/columnas mayormente sólidas)
        cols = np.nonzero(sub.mean(axis=0) > 0.5)[0]
        rows = np.nonzero(sub.mean(axis=1) > 0.5)[0]
        if len(cols) and len(rows):
            x0, x1, y0, y1 = x0 + cols[0], x0 + cols[-1] + 1, y0 + rows[0], y0 + rows[-1] + 1
        x0, y0, x1, y1 = x0 + 1, y0 + 1, x1 - 1, y1 - 1  # un pelo hacia adentro: sin bordes verdes
        found.append({"x": x0 / w, "y": y0 / h, "w": (x1 - x0) / w, "h": (y1 - y0) / h, "_a": area})
    found.sort(key=lambda r: -r["_a"])
    return _protect(found[:2])


def _close(mask, r: int):
    """Cierre morfológico (dilatar y luego erosionar r píxeles) con desplazamientos de numpy."""
    import numpy as np

    def shift_any(m, grow):
        out = m.copy()
        for _ in range(r):
            p = np.pad(out, 1, constant_values=not grow)
            nb = [p[1:-1, :-2], p[1:-1, 2:], p[:-2, 1:-1], p[2:, 1:-1], out]
            out = np.logical_or.reduce(nb) if grow else np.logical_and.reduce(nb)
        return out
    return shift_any(shift_any(mask, True), False)


def _label(mask):
    """Componentes conexas (4 vecinos) sin depender de scipy."""
    import numpy as np
    try:
        from scipy import ndimage
        return ndimage.label(mask)
    except Exception:  # noqa: BLE001
        pass
    h, w = mask.shape
    lab = np.zeros((h, w), np.int32)
    n = 0
    for y0 in range(h):
        for x0 in range(w):
            if mask[y0, x0] and not lab[y0, x0]:
                n += 1
                stack = [(y0, x0)]
                lab[y0, x0] = n
                while stack:
                    y, x = stack.pop()
                    for yy, xx in ((y - 1, x), (y + 1, x), (y, x - 1), (y, x + 1)):
                        if 0 <= yy < h and 0 <= xx < w and mask[yy, xx] and not lab[yy, xx]:
                            lab[yy, xx] = n
                            stack.append((yy, xx))
    return lab, n


def filters(ch: dict, src_lbl: str, inp: int | None, OW: int, OH: int, fps: int, dur: float,
            bg_is_image: bool, prekeyed: bool = False) -> tuple[list[str], str]:
    """Cadenas de FFmpeg: fondo (video en bucle a partir de bg.start / imagen / color) + persona recortada."""
    g = []
    if inp is not None:
        if bg_is_image:
            g.append(f"[{inp}:v]scale={OW}:{OH}:force_original_aspect_ratio=increase,crop={OW}:{OH},setsar=1,"
                     f"fps={fps},trim=duration={dur:.3f},setpts=PTS-STARTPTS,format=yuv420p[ckbg]")
        else:
            g.append(f"[{inp}:v]fps={fps},scale={OW}:{OH}:force_original_aspect_ratio=increase,crop={OW}:{OH},"
                     f"setsar=1,trim=duration={dur:.3f},setpts=PTS-STARTPTS,format=yuv420p[ckbg]")
    else:
        col = (ch["bg"].get("color") or "#101018").lstrip("#")
        g.append(f"color=c=0x{col}:s={OW}x{OH}:d={dur:.3f}:r={fps},format=yuv420p[ckbg]")
    key = ch["color"].lstrip("#")
    if prekeyed:  # ya recortado tramo por tramo (con zonas protegidas)
        fg = f"{src_lbl}format=yuva420p"
    else:
        fg = f"{src_lbl}chromakey=0x{key}:{ch['similarity']:.3f}:{ch['blend']:.3f}"
        sp = despill_type(ch["color"]) if ch["despill"] else None
        if sp:
            fg += f",despill=type={sp}:mix=0.5:expand=0"
    sc = ch["subject"]["scale"]
    if sc < 0.999:
        fg += f",scale=trunc(iw*{sc:.3f}/2)*2:trunc(ih*{sc:.3f}/2)*2"
    g.append(fg + "[ckfg]")
    x, y = ch["subject"]["x"], ch["subject"]["y"]
    g.append(f"[ckbg][ckfg]overlay=x=(W-w)*{x:.3f}:y=(H-h)*{y:.3f}:format=auto,format=yuv420p,setsar=1[ckout]")
    return g, "ckout"
