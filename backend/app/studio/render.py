"""Render del timeline del Studio con FFmpeg (multipista: toma, b-roll, textos, subtítulos, música, SFX)."""
from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

from .. import config, db, media
from ..ai import gpu
from ..editor.crop import track_expr
from ..editor.subtitles import _clean, _ts, group_words
from . import assets, chroma, projects
from . import timeline as tlm
from .style import ass_color

_graph_flag: list[str] | None = None


def graph_flag(path: Path) -> list[str]:
    """FFmpeg ≥7 usa '-/filter_complex archivo'; versiones anteriores '-filter_complex_script'."""
    global _graph_flag
    if _graph_flag is None:
        test = config.WORK_DIR / "flag_test.txt"
        test.parent.mkdir(parents=True, exist_ok=True)
        test.write_text("[0:v]null[v]", encoding="utf-8")
        r = subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=s=64x64:d=0.1",
                            "-/filter_complex", str(test), "-map", "[v]", "-f", "null", "-"],
                           capture_output=True, timeout=20)
        _graph_flag = ["-/filter_complex"] if r.returncode == 0 else ["-filter_complex_script"]
        test.unlink(missing_ok=True)
    return _graph_flag + [str(path)]


# Transiciones sin cambiar la duración (t = tiempo local del segmento / toma)
SEG_FX = {
    "zoom": ",scale=w='{OW}*(1+0.2*max(0,1-t/0.3))':h='{OH}*(1+0.2*max(0,1-t/0.3))':eval=frame,crop={OW}:{OH}",
    "glitch": ",rgbashift=rh=-16:bh=16:enable='lt(t,0.2)',noise=alls=40:allf=t:enable='lt(t,0.2)'",
    "whip": ",avgblur=sizeX=60:sizeY=1:enable='lt(t,0.14)'",
}
BROLL_FX = {
    "zoom": ",scale=w='{OW}*(1+0.18*max(0,1-t/0.35))':h='{OH}*(1+0.18*max(0,1-t/0.35))':eval=frame,crop={OW}:{OH}",
    "glitch": ",rgbashift=rh=-16:bh=16:enable='lt(t,0.2)',noise=alls=40:allf=t:enable='lt(t,0.2)'",
}
TRANSITIONS = ["cut", "zoom", "flash", "fade", "glitch", "whip", "slide"]


# ---------------- tarjetas (b-roll "card"/"pip") y fondo del chroma por tramos ----------------

def _font(size: int):
    from PIL import ImageFont
    try:
        from ..thumbs.compose import font_path
        fp = font_path()
    except Exception:  # noqa: BLE001
        fp = None
    for cand in [fp, "C:/Windows/Fonts/impact.ttf", "C:/Windows/Fonts/ariblk.ttf", "C:/Windows/Fonts/arialbd.ttf",
                 "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"]:
        if cand and Path(cand).exists():
            try:
                return ImageFont.truetype(cand, size)
            except OSError:
                continue
    try:
        return ImageFont.load_default(size)
    except TypeError:
        return ImageFont.load_default()


def _rgb(h: str, d=(16, 16, 24)) -> tuple:
    try:
        h = h.lstrip("#")
        return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))
    except (ValueError, AttributeError):
        return d


def _fit_font(draw, text: str, max_w: int, size: int, min_size: int = 14):
    while size > min_size:
        f = _font(size)
        if draw.textlength(text, font=f) <= max_w:
            return f, size
        size = int(size * 0.9)
    return _font(min_size), min_size


def media_size(path: str, mtype: str) -> tuple[int, int]:
    if mtype == "image":
        try:
            from PIL import Image
            with Image.open(path) as im:
                return im.size
        except Exception:  # noqa: BLE001
            return 1280, 720
    try:
        m = media.probe(Path(path))
    except Exception:  # noqa: BLE001
        m = {}
    return int(m.get("width") or 1280), int(m.get("height") or 720)


def card_geometry(mw: int, mh: int, o: dict, OW: int, OH: int) -> dict:
    """Medidas de una tarjeta en px de salida (la vista en vivo usa la misma fórmula).
    inner = imagen/clip, b = borde blanco, m = margen para la sombra, lab = alto del rótulo."""
    W = o["scale"] * OW
    b = round(0.022 * W) if o["frame"] == "white" else 0
    iw = W - 2 * b
    ih = iw * mh / max(mh and mw, 1)
    maxh = 0.6 * OH - 2 * b
    if ih > maxh:
        ih = maxh
        iw = ih * mw / max(mh, 1)
    iw, ih = max(2, int(iw) // 2 * 2), max(2, int(ih) // 2 * 2)
    fw, fh = iw + 2 * b, ih + 2 * b
    m = 0 if o["frame"] == "none" else max(4, round(0.04 * fw))
    lab_size = max(18, round(0.042 * OH)) if o["label"] else 0
    lab = round(lab_size * 1.5) if o["label"] else 0
    cw, ch = fw + 2 * m, fh + 2 * m + lab
    cw, ch = cw + cw % 2, ch + ch % 2
    fcx = min(max(o["pos"]["x"] * OW, fw / 2), OW - fw / 2)  # el marco no se sale del cuadro
    fcy = min(max(o["pos"]["y"] * OH, fh / 2), OH - fh / 2)
    # centro del lienzo (marco + rótulo) en la salida
    ccx, ccy = fcx, fcy + (ch / 2 - (m + fh / 2))
    return {"b": b, "iw": iw, "ih": ih, "fw": fw, "fh": fh, "m": m, "lab": lab, "lab_size": lab_size,
            "cw": cw, "ch": ch, "ccx": round(ccx, 1), "ccy": round(ccy, 1),
            "radius": round(0.05 * min(fw, fh)) if o["mode"] == "pip" else 0}


def card_png(o: dict, g: dict, out: Path, img_path: str | None = None, rotate: bool = True) -> Path:
    """Lienzo transparente con sombra, marco blanco, rótulo y (si es imagen) la imagen. Si es video, deja la
    ventana del marco para poner el clip encima."""
    from PIL import Image, ImageDraw, ImageFilter, ImageOps
    cw, ch, m, b, fw, fh = g["cw"], g["ch"], g["m"], g["b"], g["fw"], g["fh"]
    canvas = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
    rad = g["radius"]
    if o["frame"] != "none":  # sombra suave desplazada
        sh = Image.new("L", (cw, ch), 0)
        off = (round(0.012 * fw), round(0.02 * fw))
        ImageDraw.Draw(sh).rounded_rectangle([m + off[0], m + off[1], m + fw + off[0], m + fh + off[1]],
                                            radius=max(rad, 2), fill=140)
        sh = sh.filter(ImageFilter.GaussianBlur(max(2, round(0.022 * fw))))
        canvas.paste((0, 0, 0, 255), (0, 0), sh)
    if b:
        ImageDraw.Draw(canvas).rectangle([m, m, m + fw - 1, m + fh - 1], fill=(255, 255, 255, 255))
    if img_path:
        try:
            with Image.open(img_path) as im:
                pic = ImageOps.fit(im.convert("RGBA"), (g["iw"], g["ih"]), Image.LANCZOS)
        except Exception:  # noqa: BLE001
            pic = Image.new("RGBA", (g["iw"], g["ih"]), (40, 40, 48, 255))
        if rad:
            mask = Image.new("L", pic.size, 0)
            ImageDraw.Draw(mask).rounded_rectangle([0, 0, pic.width - 1, pic.height - 1], radius=rad, fill=255)
            pic.putalpha(mask)
        canvas.alpha_composite(pic, (m + b, m + b))
    if o["label"]:
        d = ImageDraw.Draw(canvas)
        f, size = _fit_font(d, o["label"], int(cw * 0.98), g["lab_size"])
        tw = d.textlength(o["label"], font=f)
        y = m + fh + max(2, round(size * 0.18))
        d.text(((cw - tw) / 2, y), o["label"], font=f, fill=(255, 255, 255, 255),
               stroke_width=max(2, size // 10), stroke_fill=(0, 0, 0, 255))
    if rotate and abs(o["rot"]) >= 0.2:
        canvas = canvas.rotate(-o["rot"], resample=Image.BICUBIC, expand=True)
        if canvas.width % 2 or canvas.height % 2:
            pad = Image.new("RGBA", (canvas.width + canvas.width % 2, canvas.height + canvas.height % 2), (0, 0, 0, 0))
            pad.paste(canvas, (0, 0))
            canvas = pad
    canvas.save(out)
    return out


def card_mask_png(g: dict, out: Path) -> Path:
    from PIL import Image, ImageDraw
    mask = Image.new("L", (g["iw"], g["ih"]), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, g["iw"] - 1, g["ih"] - 1], radius=g["radius"], fill=255)
    mask.save(out)
    return out


def card_scale_expr(o: dict, bd: float, is_image: bool) -> str | None:
    """Factor de escala en el tiempo local de la tarjeta (pop de entrada + ken burns lento)."""
    f = "1"
    if o["anim"] == "pop":
        f = "if(lt(t,0.18),0.6+0.48*t/0.18,if(lt(t,0.3),1.08-0.08*(t-0.18)/0.12,1))"
    if o["kenburns"] and is_image:
        f = f"({f})*(1+0.05*t/{max(bd, 0.1):.3f})"
    return None if f == "1" else f


def card_xy(o: dict, g: dict, start: float, OW: int, OH: int) -> tuple[str, str]:
    x0, y0 = f"({g['ccx']}-w/2)", f"({g['ccy']}-h/2)"
    e = f"pow(1-min(1,max(0,(t-{start:.3f})/0.3)),2)"
    if o["anim"] == "slide_left":  # entra desde la derecha
        return f"'{x0}+{e}*({OW}-{x0})'", f"'{y0}'"
    if o["anim"] == "slide_right":  # entra desde la izquierda
        return f"'{x0}-{e}*({x0}+w)'", f"'{y0}'"
    if o["anim"] == "slide_up":
        return f"'{x0}'", f"'{y0}+{e}*({OH}-{y0})'"
    return f"'{x0}'", f"'{y0}'"


def bg_still_png(e: dict, OW: int, OH: int, out: Path, img_path: str | None) -> Path:
    """Tramo de fondo fijo prerenderizado: tarjeta de texto o imagen (cover/contain sobre color)."""
    from PIL import Image, ImageDraw, ImageOps
    col = _rgb(e.get("bg_color", "#101018"))
    if e["kind"] == "text":
        im = Image.new("RGB", (OW, OH), col)
        d = ImageDraw.Draw(im)
        txt = e["text"].upper()
        f, size = _fit_font(d, txt, int(OW * 0.86), round(OH * 0.24), 24)
        sub = e.get("sub") or ""
        fs, ssize = _fit_font(d, sub, int(OW * 0.8), round(size * 0.32), 14) if sub else (None, 0)
        bb = d.textbbox((0, 0), txt, font=f)
        th = bb[3] - bb[1]
        total = th + (ssize * 1.6 if sub else 0)
        y = (OH - total) / 2 - bb[1]
        d.text(((OW - d.textlength(txt, font=f)) / 2, y), txt, font=f, fill=_rgb(e.get("color", "#FFFFFF"), (255, 255, 255)))
        if sub:
            d.text(((OW - d.textlength(sub, font=fs)) / 2, y + bb[3] + ssize * 0.6), sub, font=fs,
                   fill=_rgb(e.get("color", "#FFFFFF"), (255, 255, 255)))
        im.save(out)
        return out
    try:
        with Image.open(img_path) as src:
            src = src.convert("RGBA")
            if e.get("fit") == "contain":
                im = Image.new("RGB", (OW, OH), col)
                bw, bh = int(OW * 0.84), int(OH * 0.84)
                pic = ImageOps.contain(src, (bw, bh), Image.LANCZOS)
                im.paste(pic, ((OW - pic.width) // 2, (OH - pic.height) // 2), pic)
            else:
                im = ImageOps.fit(src.convert("RGB"), (OW, OH), Image.LANCZOS)
    except Exception:  # noqa: BLE001
        im = Image.new("RGB", (OW, OH), col)
    im.save(out)
    return out


BG_XFADE = 0.25


def _bg_media(e: dict, by_id: dict) -> tuple[str | None, str]:
    if e.get("video_id"):
        with db.get_conn() as conn:
            v = conn.execute("SELECT stored_path FROM videos WHERE id = ?", (e["video_id"],)).fetchone()
        return (v["stored_path"], "video") if v and Path(v["stored_path"]).exists() else (None, "")
    a = by_id.get(e.get("asset_id"))
    if a and Path(a["path"]).exists():
        return a["path"], a["media_type"]
    return None, ""


def bg_segments(sched: list[dict], ck: dict, dur: float) -> list[dict]:
    """Tramos que cubren todo el video: los del plan y, en los huecos, el fondo de siempre."""
    segs, t = [], 0.0
    for e in sched:
        a, b = max(e["start"], t), min(e["end"], dur)
        if a - t >= 0.04:
            segs.append({"kind": "default", "start": t, "end": a, "anim": "cut"})
        if b - a >= 0.04:
            segs.append({**e, "start": a, "end": b})
            t = b
    if dur - t >= 0.04:
        segs.append({"kind": "default", "start": t, "end": dur, "anim": "cut"})
    return segs


def prerender_bg(sched: list[dict], ck: dict, by_id: dict, OW: int, OH: int, fps: int, dur: float,
                 work: Path) -> Path | None:
    """Fondo del chroma por tramos → un solo video intermedio (por bloques, para no abrir cientos de archivos
    en un mismo FFmpeg). Tarjetas de texto e imágenes se prerenderizan con Pillow; los clips se recortan;
    entre tramos, corte o fundido/deslizamiento corto (xfade)."""
    segs = bg_segments(sched, ck, dur)
    if not segs:
        return None
    import hashlib
    sig = hashlib.sha1(json.dumps([segs, ck.get("bg"), OW, OH, fps], sort_keys=True, default=str).encode()).hexdigest()[:12]
    final = work / f"bg_{sig}.mp4"
    if final.exists():
        return final
    dpath, dimg = chroma.bg_source(ck)
    nv = gpu.nvenc_available()
    enc = (["-c:v", "h264_nvenc", "-preset", "p4", "-rc", "vbr", "-cq", "16", "-b:v", "0"] if nv
           else ["-c:v", "libx264", "-preset", "veryfast", "-crf", "16"])
    CH = 36
    parts = []
    for c0 in range(0, len(segs), CH):
        chunk = segs[c0:c0 + CH]
        inputs, g = [], []
        lens = []
        for i, e in enumerate(chunk):
            D = e["end"] - e["start"]
            nxt = chunk[i + 1] if i + 1 < len(chunk) else None
            tail = BG_XFADE if nxt and nxt.get("anim") in ("fade", "slide") and D > BG_XFADE * 2 and \
                (nxt["end"] - nxt["start"]) > BG_XFADE * 2 else 0.0
            L = D + tail
            lens.append((L, tail))
            k = len(inputs)
            norm = f"fps={fps},format=yuv420p,setsar=1,trim=duration={L:.3f},setpts=PTS-STARTPTS,settb=AVTB[s{i}]"
            cover = f"scale={OW}:{OH}:force_original_aspect_ratio=increase,crop={OW}:{OH}"
            kind = e.get("kind")
            if kind == "default":
                if dpath and not dimg:
                    st0 = float(ck["bg"].get("start", 0)) + e["start"]
                    inputs.append(["-stream_loop", "-1", "-ss", f"{st0:.3f}", "-t", f"{L + 0.5:.3f}", "-i", dpath])
                    g.append(f"[{k}:v]{cover},{norm}")
                elif dpath:
                    inputs.append(["-loop", "1", "-t", f"{L:.3f}", "-i", dpath])
                    g.append(f"[{k}:v]{cover},{norm}")
                else:
                    col = (ck["bg"].get("color") or "#101018").lstrip("#")
                    inputs.append(["-f", "lavfi", "-i", f"color=c=0x{col}:s={OW}x{OH}:d={L:.3f}:r={fps}"])
                    g.append(f"[{k}:v]{norm}")
                continue
            path, mtype = (None, "") if kind == "text" else _bg_media(e, by_id)
            if kind != "text" and not path:  # recurso perdido: color sólido
                col = (e.get("bg_color") or "#101018").lstrip("#")
                inputs.append(["-f", "lavfi", "-i", f"color=c=0x{col}:s={OW}x{OH}:d={L:.3f}:r={fps}"])
                g.append(f"[{k}:v]{norm}")
                continue
            if kind == "text" or mtype == "image":
                png = bg_still_png(e, OW, OH, work / f"bgs_{c0 + i}.png", path)
                kb = e.get("kenburns", kind != "text")
                if kb:
                    N = max(1, round(L * fps))
                    inputs.append(["-i", str(png)])
                    g.append(f"[{k}:v]scale={OW * 2}:{OH * 2}:flags=bicubic,zoompan=z='1+0.05*on/{N}':"
                             f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={N}:s={OW}x{OH}:fps={fps},{norm}")
                else:
                    inputs.append(["-loop", "1", "-t", f"{L:.3f}", "-i", str(png)])
                    g.append(f"[{k}:v]{norm}")
                continue
            inputs.append(["-ss", f"{float(e.get('src_in', 0)):.3f}", "-t", f"{L:.3f}", "-i", path])
            if e.get("fit") == "contain":
                col = (e.get("bg_color") or "#101018").lstrip("#")
                bw, bh = int(OW * 0.84) // 2 * 2, int(OH * 0.84) // 2 * 2
                vf = (f"scale={bw}:{bh}:force_original_aspect_ratio=decrease,"
                      f"pad={OW}:{OH}:(ow-iw)/2:(oh-ih)/2:0x{col}")
            else:
                vf = cover
            g.append(f"[{k}:v]{vf},tpad=stop_mode=clone:stop_duration={L:.3f},{norm}")
        # unir: grupos con corte (concat) y fundidos/deslizamientos entre grupos (xfade)
        groups, curg = [], [0]
        for i in range(1, len(chunk)):
            if lens[i - 1][1] > 0:
                groups.append(curg)
                curg = [i]
            else:
                curg.append(i)
        groups.append(curg)
        glabels, glens = [], []
        for gi, grp in enumerate(groups):
            if len(grp) == 1:
                glabels.append(f"[s{grp[0]}]")
            else:
                g.append("".join(f"[s{i}]" for i in grp) + f"concat=n={len(grp)}:v=1:a=0,settb=AVTB[g{gi}]")
                glabels.append(f"[g{gi}]")
            glens.append(sum(lens[i][0] for i in grp))
        cur, L = glabels[0], glens[0]
        for gi in range(1, len(groups)):
            first = chunk[groups[gi][0]]
            tr = "slideleft" if first.get("anim") == "slide" else "fade"
            g.append(f"{cur}{glabels[gi]}xfade=transition={tr}:duration={BG_XFADE}:offset={L - BG_XFADE:.3f}[x{gi}]")
            cur, L = f"[x{gi}]", L + glens[gi] - BG_XFADE
        total = sum(e["end"] - e["start"] for e in chunk)
        g.append(f"{cur}trim=duration={total:.3f},setpts=PTS-STARTPTS,format=yuv420p[out]")
        gf = work / f"bg_graph_{c0}.txt"
        gf.write_text(";\n".join(g), encoding="utf-8")
        part = work / f"bg_part_{sig}_{c0}.mp4"
        cmd = ["ffmpeg", "-y", "-v", "error"]
        for a in inputs:
            cmd += a
        cmd += graph_flag(gf) + ["-map", "[out]", "-an", "-r", str(fps)] + enc + ["-pix_fmt", "yuv420p", str(part)]
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=3600)
        if r.returncode != 0 or not part.exists():
            raise RuntimeError(f"No se pudo armar el fondo por tramos: {r.stderr.strip()[-400:]}")
        parts.append(part)
    if len(parts) == 1:
        parts[0].replace(final)
    else:
        lst = work / f"bg_{sig}.txt"
        lst.write_text("".join(f"file '{p.as_posix()}'\n" for p in parts), encoding="utf-8")
        r = subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy",
                            str(final)], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600)
        if r.returncode != 0:
            raise RuntimeError(f"No se pudo unir el fondo por tramos: {r.stderr.strip()[-300:]}")
        for p_ in parts:
            p_.unlink(missing_ok=True)
    return final


def _broll_source(b: dict, by_id: dict) -> tuple[str | None, str, bool]:
    """Ruta, tipo y si tiene audio de una toma de b-roll (recurso de la biblioteca o uno de tus videos)."""
    if b.get("video_id"):
        with db.get_conn() as conn:
            v = conn.execute("SELECT stored_path, has_audio FROM videos WHERE id = ?", (b["video_id"],)).fetchone()
        if v and Path(v["stored_path"]).exists():
            return v["stored_path"], "video", bool(v["has_audio"])
        return None, "", False
    a = by_id.get(b.get("asset_id"))
    if not a or not Path(a["path"]).exists():
        return None, "", False
    return a["path"], a["media_type"], a["media_type"] == "video"


def media_has_audio(path: str) -> bool:
    try:
        return bool(media.probe(Path(path)).get("has_audio"))
    except Exception:  # noqa: BLE001
        return False


def _even(x: float) -> int:
    return max(2, int(round(x)) // 2 * 2)


def region_rect(sw: int, sh: int, target: float, r: dict) -> tuple[int, int, int, int]:
    """Recorte con la proporción de salida que contiene la región r (fracciones) — p. ej. la cámara del stream."""
    rw, rh = r["w"] * sw, r["h"] * sh
    cx, cy = (r["x"] + r["w"] / 2) * sw, (r["y"] + r["h"] / 2) * sh
    if rw / rh > target:
        cw, ch = rw, rw / target
    else:
        cw, ch = rh * target, rh
    cw, ch = min(cw, sw), min(ch, sh)
    x = min(max(cx - cw / 2, 0), sw - cw)
    y = min(max(cy - ch / 2, 0), sh - ch)
    return _even(cw), _even(ch), int(x), int(y)


PUNCH_IN, PUNCH_OUT = 0.12, 0.28  # acercar rápido (ease-out) y volver suave


def zoom_terms(s: dict) -> tuple[list[str], list[dict]]:
    """Términos del factor de zoom (expresión FFmpeg en función de t local) y lista de sacudidas.
    Golpe de zoom profesional = "punch-in and hold": entra en 0.12 s, se sostiene y sale suave (no rebota)."""
    drift = float(s.get("drift") or 0)
    ln = max(0.1, s["src_out"] - s["src_in"])
    terms = []
    if drift:
        terms.append(f"{abs(drift):.3f}*" + (f"min(t/{ln:.3f},1)" if drift > 0 else f"max(1-t/{ln:.3f},0)"))
    shakes = []
    for f in s.get("fx") or []:
        if f["type"] == "punch":
            t0, a = float(f["t"]), float(f["amount"])
            hold = float(f.get("hold") or 1.1)
            rin = max(0.06, min(3.0, float(f.get("rise") or PUNCH_IN)))  # zoom progresivo (ramp): sube más lento
            t1 = t0 + rin + hold
            rise = f"(1-pow(1-clip((t-{t0:.3f})/{rin:.3f},0,1),3))"
            fall = f"clip((t-{t1:.3f})/{PUNCH_OUT},0,1)"
            terms.append(f"{a:.3f}*{rise}*(1-{fall}*{fall}*(3-2*{fall}))")
        else:
            shakes.append(f)
    return terms, shakes


def motion_filter(s: dict, OW: int, OH: int, fy: float | None = None) -> str:
    """Movimiento dentro del segmento sin cambiar su duración: empuje lento (drift), golpes de zoom que se
    sostienen (punch) y sacudidas cortas y suaves (shake). Escala a tamaños pares (sin temblor de redondeo).
    Devuelve '' si no hay movimiento."""
    terms, shakes = zoom_terms(s)
    if not terms and not shakes:
        return ""
    k = OW / 1080
    sx = sy = "0"
    if shakes:
        env = lambda f: f"exp(-pow((t-{float(f['t']) + 0.06:.3f})/0.13,2))"  # noqa: E731 — ~0.35 s
        amp = lambda f: min(16.0, float(f["amount"]) * 70) * k  # noqa: E731 — px, nunca exagerado
        sx = "+".join(f"{amp(f):.1f}*(sin(t*37)+0.45*sin(t*59))*{env(f)}" for f in shakes)
        sy = "+".join(f"{amp(f) * 0.7:.1f}*(cos(t*31)+0.45*cos(t*53))*{env(f)}" for f in shakes)
        terms += [f"{amp(f) * 3.2 / OW:.4f}*{env(f)}" for f in shakes]  # margen solo mientras sacude
    F = "1+" + "+".join(terms)
    # el zoom se centra en la altura de la cara (la cara queda quieta y no se le corta la cabeza)
    fy = 0.5 if fy is None else max(0.25, min(0.65, float(fy)))
    return (f",scale=w='2*trunc({OW}*({F})/2)':h='2*trunc({OH}*({F})/2)':eval=frame:flags=bicubic,"
            f"crop={OW}:{OH}:x='max(0,min(iw-{OW},(iw-{OW})/2+({sx})))':y='max(0,min(ih-{OH},(ih-{OH})*{fy:.3f}+({sy})))'")


def crop_rect(sw: int, sh: int, target: float, cx_frac: float, zoom: float,
              cy_frac: float | None = None) -> tuple[int, int, int, int]:
    if sw / sh > target:
        ch, cw = sh, sh * target
    else:
        cw, ch = sw, sw / target
    cw, ch = cw / zoom, ch / zoom
    cx = cx_frac * sw
    cy = sh * (cy_frac if cy_frac is not None else (0.42 if zoom > 1.0 else 0.5))
    x = min(max(cx - cw / 2, 0), sw - cw)
    y = min(max(cy - ch / 2, 0), sh - ch)
    return _even(cw), _even(ch), int(x), int(y)


# ---------------- encuadres (layouts) ----------------

LAYOUTS = ("crop", "blur", "fit", "split")
CAM_FRAC = 680 / 1920  # alto de la cámara en el modo stream (cámara arriba, juego abajo)


def layout_of(tl: dict) -> dict:
    lay = dict(tl.get("layout") or {})
    if lay.get("mode") not in LAYOUTS:
        lay["mode"] = "crop"
    if lay["mode"] == "split" and not lay.get("cam"):
        lay["mode"] = "blur"  # sin recuadro de cámara no hay modo stream
    lay["game_x"] = float(lay.get("game_x", 0.5))
    return lay


def fit_box(sw: int, sh: int, OW: int, OH: int) -> tuple[int, int]:
    r = min(OW / sw, OH / sh)
    return _even(sw * r), _even(sh * r)


def split_geometry(sw: int, sh: int, OW: int, OH: int, lay: dict) -> dict:
    cam_h = _even(OH * CAM_FRAC)
    game_h = OH - cam_h
    c = lay["cam"]
    cx, cy = int(c["x"] * sw), int(c["y"] * sh)
    cw, ch = max(2, int(c["w"] * sw)) // 2 * 2, max(2, int(c["h"] * sh)) // 2 * 2
    gh = sh // 2 * 2
    gw = min(sw, int(sh * OW / game_h)) // 2 * 2
    gx = int(min(max(lay["game_x"] * sw - gw / 2, 0), sw - gw))
    return {"cam_h": cam_h, "game_h": game_h, "cam": (cw, ch, cx, cy), "game": (gw, gh, gx, 0)}


def seg_video_chain(src: str, s: dict, i: int, lay: dict, sw: int, sh: int, OW: int, OH: int,
                    target: float, fps: int, key_hex: str | None = None, ck: dict | None = None) -> str:
    """Con `ck` (pantalla verde) el recorte se hace aquí, sobre la toma original, y el tramo sale con alfa."""
    if ck:
        out = _seg_video_chain("\x00SRC", s, i, lay, sw, sh, OW, OH, target, fps, ck["color"].lstrip("#"))
        return out.replace("format=yuv420p", "format=yuva420p").replace("\x00SRC", src + chroma.pre_key(ck, i, sw, sh))
    return _seg_video_chain(src, s, i, lay, sw, sh, OW, OH, target, fps, key_hex)


def _seg_video_chain(src: str, s: dict, i: int, lay: dict, sw: int, sh: int, OW: int, OH: int,
                     target: float, fps: int, key_hex: str | None = None) -> str:
    """Cadena de video de un segmento según el encuadre. El movimiento (zoom/sacudida) se aplica solo a la
    imagen principal: en "desenfocado" no se mueve el fondo y en "stream" no se mueve la cámara."""
    mode = s.get("layout") if s.get("layout") in LAYOUTS else lay["mode"]
    if key_hex and mode in ("blur", "split"):  # con pantalla verde el fondo lo pone el chroma, no el desenfoque
        mode = "fit"
    if s.get("rect"):  # zoom a una región (cámara del stream): pantalla completa con esa región
        cw, ch, x, y = region_rect(sw, sh, target, s["rect"])
        return (f"{src},crop={cw}:{ch}:{x}:{y},scale={OW}:{OH},setsar=1,fps={fps},format=yuv420p"
                f"{motion_filter(s, OW, OH)}")
    if mode == "crop":
        cw, ch, x, y = crop_rect(sw, sh, target, s.get("crop_x", 0.5), s.get("zoom", 1.0), s.get("crop_y"))
        xexpr = str(x)
        if s.get("track"):  # seguimiento dinámico del rostro (tiempos relativos al segmento)
            xexpr = "'" + track_expr(s["track"], cw, sw, t0=s["src_in"]) + "'"
        fy = (float(s["face_y"]) * sh - y) / max(ch, 1) if isinstance(s.get("face_y"), (int, float)) else None
        return (f"{src},crop={cw}:{ch}:{xexpr}:{y},scale={OW}:{OH},setsar=1,fps={fps},format=yuv420p"
                f"{motion_filter(s, OW, OH, fy)}")
    if mode == "split":
        gm = split_geometry(sw, sh, OW, OH, lay)
        cw, ch, cx, cy = gm["cam"]
        gw, gh, gx, gy = gm["game"]
        return (f"{src},fps={fps},split[cs{i}][gs{i}];"
                f"[cs{i}]crop={cw}:{ch}:{cx}:{cy},scale={OW}:{gm['cam_h']}:force_original_aspect_ratio=increase,"
                f"crop={OW}:{gm['cam_h']},setsar=1[cm{i}];"
                f"[gs{i}]crop={gw}:{gh}:{gx}:{gy},scale={OW}:{gm['game_h']},setsar=1"
                f"{motion_filter(s, OW, gm['game_h'])}[gm{i}];"
                f"[cm{i}][gm{i}]vstack,setsar=1,format=yuv420p")
    # blur / fit: la toma completa centrada; zoom del segmento = la imagen crece (se recorta a lo ancho)
    fw, fh = fit_box(sw, sh, OW, OH)
    z = max(1.0, float(s.get("zoom", 1.0)))
    zw, zh = _even(fw * z), _even(fh * z)
    vw, vh = min(OW, zw), min(OH, zh)
    pos = s.get("pos") or {"x": 0.5, "y": 0.5}
    px = int(float(pos.get("x", 0.5)) * max(0, OW - vw))
    py = int(float(pos.get("y", 0.5)) * max(0, OH - vh))
    vx = int(min(max(float(s.get("crop_x", 0.5)) * zw - vw / 2, 0), zw - vw))
    fg = f"scale={zw}:{zh},crop={vw}:{vh}:{vx}:{(zh - vh) // 2},setsar=1{motion_filter(s, vw, vh)}"
    if mode == "fit":
        pad = "black@0" if key_hex else "black"
        return (f"{src},fps={fps},{fg},pad={OW}:{OH}:{px}:{py}:{pad},setsar=1,format=yuv420p")
    bw, bh = _even(OW / 6), _even(OH / 6)
    return (f"{src},fps={fps},split[bs{i}][fs{i}];"
            f"[bs{i}]scale={bw}:{bh}:force_original_aspect_ratio=increase,crop={bw}:{bh},boxblur=6:2,"
            f"eq=brightness=-0.07:saturation=1.15,scale={OW}:{OH},setsar=1[bb{i}];"
            f"[fs{i}]{fg}[ff{i}];"
            f"[bb{i}][ff{i}]overlay={px}:{py}:eval=frame,setsar=1,format=yuv420p")


# ---------------- subtítulos y textos (ASS) ----------------

def caption_margin(tl: dict, st: dict, cap_size: int) -> int:
    """Distancia desde abajo de los subtítulos. "auto" los pone donde no tapan lo importante:
    en stream, justo debajo de la cámara; con fondo desenfocado, bajo la imagen; recortado, en el tercio inferior."""
    W, H = tl["width"], tl["height"]
    pos = st.get("caption_position", "auto")
    if pos in ("bottom", "middle", "top"):
        return int(H * {"bottom": 0.27, "middle": 0.45, "top": 0.68}[pos])
    if W >= H:  # horizontal (YouTube)
        return int(H * 0.09)
    mode = layout_of(tl)["mode"]
    if mode == "split":
        center = H * CAM_FRAC + cap_size * 1.4
    elif mode in ("blur", "fit"):
        ar = float((tl.get("layout") or {}).get("src_ar") or 16 / 9)
        center = (H + W / ar) / 2 + cap_size * 1.3
    else:
        return int(H * 0.28)
    return max(int(H * 0.08), int(H - center - cap_size / 2))


def fit_text(text: str, fs: float, width: float, max_lines: int = 3, char_w: float = 0.62) -> tuple[str, int]:
    """Que el texto quepa en el cuadro: parte en líneas (\\N) y, si hace falta, baja el tamaño (hasta 45 %).
    char_w = ancho medio de una letra en mayúsculas gruesas respecto al tamaño de fuente."""
    words = str(text).split()
    if not words:
        return "", int(fs)
    size = float(fs)
    while True:
        per = max(4, int(width / (char_w * size)))
        lines, cur = [], ""
        for w in words:
            cand = f"{cur} {w}".strip()
            if len(cand) <= per or not cur:
                cur = cand
            else:
                lines.append(cur)
                cur = w
        lines.append(cur)
        ok = len(lines) <= max_lines and all(len(x) <= per for x in lines)
        if ok or size <= fs * 0.45:
            return "\\N".join(lines), int(size)
        size *= 0.92


def build_ass(tl: dict, st: dict) -> str:
    from .style import readable
    st = readable(dict(st))  # nunca letra del mismo color que la caja del título
    W, H = tl["width"], tl["height"]
    k = min(W, H) / 1080
    cap_size = int(st["caption_size"] * k)
    margin_v = caption_margin(tl, st, cap_size)
    white, black = ass_color(st["caption_color"]), "&H00000000"
    lines = [
        "[Script Info]", "ScriptType: v4.00+", f"PlayResX: {W}", f"PlayResY: {H}", "WrapStyle: 0",
        "ScaledBorderAndShadow: yes", "", "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, "
        "Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, "
        "MarginR, MarginV, Encoding",
        f"Style: Cap,{st['caption_font']},{cap_size},{white},{white},{black},&H80000000,-1,0,0,0,100,100,0,0,1,"
        f"{max(3, int(7 * k))},{max(2, int(4 * k))},2,{int(60 * k)},{int(60 * k)},{margin_v},1",
        f"Style: CapClean,Segoe UI Semibold,{int(cap_size * 0.92)},{white},{white},&H64000000,&H64000000,-1,0,0,0,"
        f"100,100,0,0,3,{max(6, int(12 * k))},0,2,{int(160 * k)},{int(160 * k)},{margin_v},1",
        f"Style: Title,{st['title_font']},{int(66 * k)},{ass_color(st['title_color'])},{white},"
        f"{ass_color(st['title_bg'])},{ass_color(st['title_bg'])},-1,0,0,0,100,100,0,0,3,{int(20 * k)},0,8,"
        f"{int(80 * k)},{int(80 * k)},{int(H * 0.12)},1",
        f"Style: Callout,{st['title_font']},{int(cap_size * 1.25)},{ass_color(st['callout_color'])},{white},{black},"
        f"&H96000000,-1,0,0,0,100,100,0,0,1,{max(3, int(8 * k))},{max(1, int(4 * k))},5,{int(60 * k)},{int(60 * k)},0,1",
        f"Style: Meme,Impact,{int(cap_size * 1.9)},{ass_color(st['callout_color'])},{white},{black},"
        f"&H96000000,-1,0,0,0,100,100,1,0,1,{max(5, int(11 * k))},{max(2, int(6 * k))},5,{int(40 * k)},{int(40 * k)},0,1",
        f"Style: Lower,{st['title_font']},{int(48 * k)},{ass_color(st['title_color'])},{white},"
        f"{ass_color(st['title_bg'])},{ass_color(st['title_bg'])},-1,0,0,0,100,100,0,0,3,{int(14 * k)},0,1,"
        f"{int(60 * k)},{int(60 * k)},{int(H * 0.2)},1",
        "", "[Events]", "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    split_cam_h = int(H * CAM_FRAC) if W < H and layout_of(tl)["mode"] == "split" else 0
    pop = "{\\fad(80,160)\\fscx70\\fscy70\\t(0,140,\\fscx108\\fscy108)\\t(140,240,\\fscx100\\fscy100)}"
    for t in tl["tracks"]["text"]:
        style = {"title": "Title", "callout": "Callout", "lower": "Lower", "meme": "Meme"}.get(t.get("kind"), "Callout")
        text = _clean(t["text"].upper() if style != "Lower" else t["text"])
        # Callouts arriba del centro para no chocar con los subtítulos
        tag = pop + ("{\\pos(%d,%d)}" % (W // 2, int(H * (0.6 if split_cam_h else 0.36))) if style == "Callout" else "")
        if style == "Title" and split_cam_h:  # en modo stream: abajo de la cámara, sin tapar la cara
            tag = pop + "{\\an2\\pos(%d,%d)}" % (W // 2, int(split_cam_h - 28 * k))
        if style == "Meme":  # entra grande, rebota y queda inclinado; posición variada según el texto
            rot = (-6, 5, -3, 7)[len(text) % 4]
            yy = (0.30, 0.62, 0.40)[len(text) % 3]
            tag = ("{\\fad(40,180)\\frz%d\\fscx160\\fscy160\\t(0,120,\\fscx92\\fscy92)"
                   "\\t(120,220,\\fscx104\\fscy104)\\t(220,300,\\fscx100\\fscy100)\\pos(%d,%d)}" % (rot, W // 2, int(H * yy)))
        free = t.get("pos") if isinstance(t.get("pos"), dict) else None
        sc = max(0.3, min(float(t.get("scale") or 1), 4.0))
        base = {"Title": 66 * k, "Callout": cap_size * 1.25, "Meme": cap_size * 1.9, "Lower": 48 * k}[style]
        avail = W * (0.80 if style == "Lower" else 0.88) - (2 * 20 * k if style in ("Title", "Lower") else 0)
        text, size = fit_text(text, base * sc, avail, 2 if style == "Lower" else 3,
                              0.55 if style == "Meme" else 0.70)  # fuentes gruesas: mejor de menos que salirse
        if free or size != int(base):  # tamaño ajustado al cuadro y/o movido/escalado a mano en el editor
            fs = "\\fs%d" % size if size != int(base) else ""
            if free:
                x = int(max(0.0, min(float(free.get("x", 0.5)), 1.0)) * W)
                y = int(max(0.0, min(float(free.get("y", 0.5)), 1.0)) * H)
                keep = "{\\fad(40,180)}" if style == "Meme" else pop
                rot = ("\\frz%d" % (-6, 5, -3, 7)[len(text) % 4]) if style == "Meme" else ""
                tag = keep + "{\\an5\\pos(%d,%d)%s%s}" % (x, y, rot, fs)
            else:
                tag = tag + "{%s}" % fs
        lines.append(f"Dialogue: 2,{_ts(t['start'])},{_ts(t['end'])},{style},,0,0,0,,{tag}{text}")

    if tl["captions"].get("enabled", True) and st["caption_style"] != "none" and tl["words"]:
        hl = ass_color(st["highlight_color"])
        clean = st["caption_style"] == "clean"
        for g in group_words(tl["words"], int(st["caption_max_words"])):
            words = [_clean(w["word"].upper() if st["caption_uppercase"] and not clean else w["word"]) for w in g]
            g0, g1 = g[0]["start"], g[-1]["end"]
            if clean:  # videos largos: frase en caja translúcida; la palabra activa solo cambia de color
                for i, w in enumerate(g):
                    a = w["start"] if i else g0
                    b = g[i + 1]["start"] if i + 1 < len(g) else g1
                    if b <= a:
                        continue
                    parts = [f"{{\\c{hl}}}{x}{{\\c{white}}}" if j == i else x for j, x in enumerate(words)]
                    lines.append(f"Dialogue: 1,{_ts(a)},{_ts(b)},CapClean,,0,0,0,,{' '.join(parts)}")
                continue
            if st["caption_style"] == "simple":
                lines.append(f"Dialogue: 1,{_ts(g0)},{_ts(g1)},Cap,,0,0,0,,{' '.join(words)}")
                continue
            for i, w in enumerate(g):
                a = w["start"] if i else g0
                b = g[i + 1]["start"] if i + 1 < len(g) else g1
                if b <= a:
                    continue
                # palabra activa: color de resalte, más grande y con un rebote corto
                parts = [f"{{\\c{hl}\\fscx118\\fscy118\\t(0,70,\\fscx112\\fscy112)}}{x}{{\\c{white}\\fscx100\\fscy100}}"
                         if j == i else x for j, x in enumerate(words)]
                enter = "{\\fscx86\\fscy86\\t(0,90,\\fscx100\\fscy100)}" if i == 0 else ""  # el grupo entra con un pop
                lines.append(f"Dialogue: 1,{_ts(a)},{_ts(b)},Cap,,0,0,0,,{enter}{' '.join(parts)}")
    return "\n".join(lines) + "\n"


# ---------------- grafo de filtros ----------------

def build_command(p: dict, quality: str, work: Path, out: Path, force_single: bool = False,
                  stems: dict | None = None) -> tuple[list[str], float, str]:
    tl, st, meta = p["timeline"], p["style"], p["source_meta"]
    segs = tl["tracks"]["video"]
    if not segs:
        raise RuntimeError("El timeline está vacío.")
    scale = 2 / 3 if quality == "preview" else 1.0  # vista previa rápida 720p; "hd" a resolución completa
    if quality == "final" and tl["width"] > tl["height"] and tl["height"] <= 1080:  # videos de YouTube (horizontal)
        # Exportación final a 1440p: YouTube comprime con más calidad (VP9/AV1) los videos de 1440p o más;
        # un 1080p se queda con su compresión más baja aunque la grabación original se vea nítida.
        scale = 4 / 3
    OW, OH = _even(tl["width"] * scale), _even(tl["height"] * scale)
    fps = tl["fps"]
    if quality == "final" and float(meta.get("fps") or 0) >= 50:
        fps = 60  # tu grabación es de 60 fps: el video final también (antes salía a 30)
    dur = tl["duration"]
    target = tl["width"] / tl["height"]
    is_video = p["source_kind"] == "video" and meta.get("has_video")
    has_voice = bool(meta.get("has_audio"))
    by_id = {a["id"]: a for a in assets.list_assets()}

    inputs: list[list[str]] = []

    def add(args: list[str]) -> int:
        inputs.append(args)
        return len(inputs) - 1

    g: list[str] = []
    n = len(segs)
    # Fuentes largas (streams) o segmentos desordenados: una entrada por segmento con -ss (busca directo,
    # sin decodificar horas de video). Fuentes cortas: una sola entrada con split+trim.
    ordered = all(segs[i]["src_in"] <= segs[i + 1]["src_in"] for i in range(n - 1))
    per_seg = ((meta.get("duration") or 0) > 180 or not ordered) and not force_single
    if per_seg:
        # Windows limita la línea de comandos a ~32 000 caracteres: con muchos cortes, un enlace corto a la toma
        # (en la carpeta de trabajo, que es el directorio actual de FFmpeg) evita repetir la ruta completa.
        src_arg = p["source_path"]
        if len(segs) > 40:
            link = work / ("src" + Path(src_arg).suffix.lower())
            try:
                if not link.exists():
                    os.link(src_arg, link)
                src_arg = link.name
            except OSError:
                pass
        seg_in = [add(["-ss", f"{s['src_in']:.3f}", "-t", f"{s['src_out'] - s['src_in']:.3f}",
                       "-i", src_arg]) for s in segs]
        src = seg_in[0]
    else:
        src = add(["-i", p["source_path"]])

    def vin(i: int) -> str:
        return f"[{seg_in[i]}:v]" if per_seg else f"[vs{i}]"

    def ain(i: int) -> str:
        return f"[{seg_in[i]}:a]" if per_seg else f"[as{i}]"

    def vtrim(s: dict) -> str:
        return "setpts=PTS-STARTPTS" if per_seg else \
            f"trim=start={s['src_in']:.3f}:end={s['src_out']:.3f},setpts=PTS-STARTPTS"

    def atrim(s: dict) -> str:
        return "asetpts=PTS-STARTPTS" if per_seg else \
            f"atrim=start={s['src_in']:.3f}:end={s['src_out']:.3f},asetpts=PTS-STARTPTS"

    if is_video:
        sw, sh = meta["width"], meta["height"]
        if not per_seg:
            g.append(f"[{src}:v]" + (f"split={n}" if n > 1 else "null") + "".join(f"[vs{i}]" for i in range(n)))
        lay = layout_of(tl)
        ck = chroma.active(tl)
        key_hex = ck["color"].lstrip("#") if ck else None
        for i, s in enumerate(segs):
            fx = SEG_FX.get(s.get("transition", "cut") if i else "cut", "").format(OW=OW, OH=OH)
            g.append(seg_video_chain(f"{vin(i)}{vtrim(s)}", s, i, lay, sw, sh, OW, OH, target, fps, None, ck)
                     + f"{fx},setsar=1[v{i}]")
    else:
        c1, c2 = st["bg_color_1"].lstrip("#"), st["bg_color_2"].lstrip("#")
        gi = add(["-f", "lavfi", "-i", f"gradients=s={OW}x{OH}:c0=0x{c1}:c1=0x{c2}:x0=0:y0=0:x1={OW}:y1={OH}:"
                                      f"speed=0.006:d={dur:.3f}:r={fps}"])
        g.append(f"[{gi}:v]format=yuv420p,setsar=1[vbase]")

    if has_voice:
        if not per_seg:
            g.append(f"[{src}:a]" + (f"asplit={n}" if n > 1 else "anull") + "".join(f"[as{i}]" for i in range(n)))
        for i, s in enumerate(segs):
            ln = s["src_out"] - s["src_in"]
            g.append(f"{ain(i)}{atrim(s)},"
                     f"aformat=sample_rates=48000:channel_layouts=stereo,afade=t=in:d=0.015,"
                     f"afade=t=out:st={max(ln - 0.03, 0):.3f}:d=0.03[a{i}]")
    else:  # video sin audio: silencio con la misma duración por segmento
        for i, s in enumerate(segs):
            ln = s["src_out"] - s["src_in"]
            g.append(f"anullsrc=r=48000:cl=stereo,atrim=duration={ln:.3f},asetpts=PTS-STARTPTS[a{i}]")

    # Con Demucs: la voz puede venir remezclada (realzada o sin música de fondo), alineada con el timeline
    use_mix = bool(stems and stems.get("mix"))
    vlabel = "voiceorig" if use_mix else "voice0"
    ck_audio = None
    if is_video:
        ck = chroma.active(tl)
        g.append("".join(f"[v{i}][a{i}]" for i in range(n)) + f"concat=n={n}:v=1:a=1[{'vkey' if ck else 'vbase'}][{vlabel}]")
        sched = tlm.bg_schedule((ck or {}).get("bg_schedule") or (tl.get("chroma") or {}).get("bg_schedule"), dur) \
            if ck else []
        if ck and sched:  # fondo por tramos (recursos del tema): video intermedio como fondo
            bgv = prerender_bg(sched, ck, by_id, OW, OH, fps, dur, work)
            si = add(["-i", str(bgv)])
            cg, _ = chroma.filters(ck, "[vkey]", None, OW, OH, fps, dur, False, prekeyed=True)
            cg[0] = (f"[{si}:v]fps={fps},scale={OW}:{OH},setsar=1,format=yuv420p,trim=duration={dur:.3f},"
                     f"setpts=PTS-STARTPTS,tpad=stop_mode=clone:stop_duration=1[ckbg]")
            g += cg
            g.append("[ckout]null[vbase]")
        elif ck:  # pantalla verde: fondo nuevo (gameplay/video/imagen/color) detrás de la persona
            bpath, bimg = chroma.bg_source(ck)
            bi = None
            if bpath:
                bi = add(["-loop", "1", "-t", f"{dur:.3f}", "-i", bpath] if bimg else
                         ["-stream_loop", "-1", "-ss", f"{ck['bg']['start']:.3f}", "-i", bpath])
            cg, _ = chroma.filters(ck, "[vkey]", bi, OW, OH, fps, dur, bimg, prekeyed=True)
            g += cg
            g.append("[ckout]null[vbase]")
            if bi is not None and not bimg and ck["bg"]["volume"] > 0 and media_has_audio(bpath):
                g.append(f"[{bi}:a]aformat=sample_rates=48000:channel_layouts=stereo,atrim=duration={dur:.3f},"
                         f"asetpts=PTS-STARTPTS,volume={ck['bg']['volume']:.3f}[ckaud]")
                ck_audio = "[ckaud]"
    else:
        g.append("".join(f"[a{i}]" for i in range(n)) + f"concat=n={n}:v=0:a=1[{vlabel}]")
    if use_mix:
        g.append("[voiceorig]anullsink")
        mi = add(["-i", str(stems["mix"])])
        g.append(f"[{mi}:a]aformat=sample_rates=48000:channel_layouts=stereo,apad,atrim=duration={dur:.3f}[voice0]")
    from ..editor import master
    vchain = (master.chain(bool(st.get("clean_voice"))) + ",") if st.get("pro_audio", True) else ""
    g.append(f"[voice0]{vchain}volume={st['voice_volume']}[voice]")

    cur = "vbase"
    flash_k = 0

    def flash(at: float, color: str = "white") -> None:
        nonlocal cur, flash_k
        a0 = max(0.0, at - 0.07)
        g.append(f"color=c={color}:s={OW}x{OH}:d=0.42:r={fps},format=yuva420p,"
                 f"fade=t=in:st=0:d=0.07:alpha=1,fade=t=out:st=0.08:d=0.32:alpha=1,"
                 f"setpts=PTS+{a0:.3f}/TB[fl{flash_k}]")
        g.append(f"[{cur}][fl{flash_k}]overlay=eof_action=pass:enable='between(t,{a0:.3f},{a0 + 0.42:.3f})'[flo{flash_k}]")
        cur = f"flo{flash_k}"
        flash_k += 1

    # Transiciones de la toma principal que van por encima (destello / fundido a negro)
    for i, s in enumerate(segs):
        tr = s.get("transition", "cut") if i else "cut"
        if tr == "flash":
            flash(s["start"])
        elif tr == "fade":
            flash(s["start"], "black")

    broll_audio = []
    for k, b in enumerate(tl["tracks"]["broll"]):
        path, mtype, b_has_audio = _broll_source(b, by_id)
        if not path:
            continue
        bd = b["end"] - b["start"]
        bo = tlm.broll_opts(b, mtype == "image")
        if bo["mode"] in ("card", "pip"):  # tarjeta encima (imagen/clip con marco, sombra, rótulo y animación)
            mw, mh = media_size(path, mtype)
            cg = card_geometry(mw, mh, bo, OW, OH)
            sc = card_scale_expr(bo, bd, mtype == "image")
            tail = (f",scale=w='2*trunc(iw*{sc}/2)':h=-2:eval=frame" if sc else "") + \
                (",fade=t=in:st=0:d=0.25:alpha=1" if bo["anim"] == "fade" else "") + \
                f",fade=t=out:st={max(bd - 0.15, 0):.3f}:d=0.15:alpha=1,setpts=PTS-STARTPTS+{b['start']:.3f}/TB[bv{k}]"
            if mtype == "image":
                png = card_png(bo, cg, work / f"card{k}.png", img_path=path)
                ci = add(["-loop", "1", "-t", f"{bd:.3f}", "-i", str(png)])
                g.append(f"[{ci}:v]fps={fps},format=rgba,trim=duration={bd:.3f}{tail}")
                idx = ci
            else:
                idx = add(["-ss", f"{float(b.get('src_in', 0)):.3f}", "-t", f"{bd:.3f}", "-i", path])
                png = card_png(bo, cg, work / f"card{k}.png", rotate=False)
                fi = add(["-loop", "1", "-t", f"{bd:.3f}", "-i", str(png)])
                g.append(f"[{idx}:v]scale={cg['iw']}:{cg['ih']}:force_original_aspect_ratio=increase,"
                         f"crop={cg['iw']}:{cg['ih']},setsar=1,fps={fps},format=rgba,tpad=stop_mode=clone:stop_duration={bd:.3f},"
                         f"trim=duration={bd:.3f},"
                         f"setpts=PTS-STARTPTS[cv{k}]")
                clip = f"[cv{k}]"
                if cg["radius"]:
                    mi = add(["-loop", "1", "-t", f"{bd:.3f}", "-i", str(card_mask_png(cg, work / f"cmask{k}.png"))])
                    g.append(f"[{mi}:v]fps={fps},format=gray,trim=duration={bd:.3f},setpts=PTS-STARTPTS[cm{k}]")
                    g.append(f"[cv{k}][cm{k}]alphamerge[cva{k}]")
                    clip = f"[cva{k}]"
                rot = ""
                if abs(bo["rot"]) >= 0.2:
                    ang = f"{bo['rot'] * 3.14159265 / 180:.5f}"
                    rot = f",rotate=a={ang}:c=none:ow='2*trunc(rotw({ang})/2)':oh='2*trunc(roth({ang})/2)'"
                g.append(f"[{fi}:v]fps={fps},format=rgba,trim=duration={bd:.3f},setpts=PTS-STARTPTS[cf{k}]")
                g.append(f"[cf{k}]{clip}overlay={cg['m'] + cg['b']}:{cg['m'] + cg['b']}:format=auto:shortest=1,"
                         f"format=rgba{rot}{tail}")
            ox, oy = card_xy(bo, cg, b["start"], OW, OH)
            g.append(f"[{cur}][bv{k}]overlay=x={ox}:y={oy}:eval=frame:eof_action=pass:format=auto:"
                     f"enable='between(t,{b['start']:.3f},{b['end']:.3f})'[ov{k}]")
            cur = f"ov{k}"
            vol = float(b.get("volume", 0) or 0)
            if vol > 0 and b_has_audio and mtype != "image":
                g.append(f"[{idx}:a]atrim=duration={bd:.3f},asetpts=PTS-STARTPTS,"
                         f"aformat=sample_rates=48000:channel_layouts=stereo,volume={vol:.3f},"
                         f"afade=t=in:d=0.08,afade=t=out:st={max(bd - 0.12, 0):.3f}:d=0.12,"
                         f"adelay=delays={int(b['start'] * 1000)}:all=1[ba{k}]")
                broll_audio.append(f"[ba{k}]")
            continue
        if mtype == "image":
            idx = add(["-loop", "1", "-t", f"{bd:.3f}", "-i", path])
        else:
            idx = add(["-ss", f"{float(b.get('src_in', 0)):.3f}", "-t", f"{bd:.3f}", "-i", path])
        if b.get("fit") == "blur":
            g.append(f"[{idx}:v]split[bb{k}][bf{k}]")
            g.append(f"[bb{k}]scale={OW}:{OH}:force_original_aspect_ratio=increase,crop={OW}:{OH},boxblur=22:3,"
                     f"eq=brightness=-0.08[bbg{k}]")
            g.append(f"[bf{k}]scale={OW}:{OH}:force_original_aspect_ratio=decrease[bfg{k}]")
            g.append(f"[bbg{k}][bfg{k}]overlay=(W-w)/2:(H-h)/2[bsrc{k}]")
            src_lbl = f"[bsrc{k}]"
        else:
            src_lbl = f"[{idx}:v]"
            vf_cover = f"scale={OW}:{OH}:force_original_aspect_ratio=increase,crop={OW}:{OH},"
            if bo["kenburns"] and mtype == "image":  # zoom lento sobre la foto
                vf_cover = (f"scale={OW}:{OH}:force_original_aspect_ratio=increase,crop={OW}:{OH},"
                            f"scale=w='2*trunc({OW}*(1+0.05*t/{max(bd, 0.1):.3f})/2)':h=-2:eval=frame,"
                            f"crop={OW}:{OH},")
        tr = b.get("transition", "cut")
        fx = BROLL_FX.get(tr, "").format(OW=OW, OH=OH)
        alpha = ",format=yuva420p,fade=t=in:st=0:d=0.25:alpha=1" if tr == "fade" else ",format=yuva420p"
        g.append(f"{src_lbl}{'' if b.get('fit') == 'blur' else vf_cover}setsar=1,fps={fps},format=yuv420p,"
                 f"trim=duration={bd:.3f}{fx}{alpha},setpts=PTS-STARTPTS+{b['start']:.3f}/TB[bv{k}]")
        xpos = "0"
        if tr == "slide":
            xpos = f"'if(lt(t,{b['start'] + 0.22:.3f}),W*(1-(t-{b['start']:.3f})/0.22),0)'"
        g.append(f"[{cur}][bv{k}]overlay=x={xpos}:y=0:eof_action=pass:"
                 f"enable='between(t,{b['start']:.3f},{b['end']:.3f})'[ov{k}]")
        cur = f"ov{k}"
        if tr == "flash":
            flash(b["start"])
        vol = float(b.get("volume", 0) or 0)
        if vol > 0 and b_has_audio:
            g.append(f"[{idx}:a]atrim=duration={bd:.3f},asetpts=PTS-STARTPTS,"
                     f"aformat=sample_rates=48000:channel_layouts=stereo,volume={vol:.3f},"
                     f"afade=t=in:d=0.08,afade=t=out:st={max(bd - 0.12, 0):.3f}:d=0.12,"
                     f"adelay=delays={int(b['start'] * 1000)}:all=1[ba{k}]")
            broll_audio.append(f"[ba{k}]")

    ass = build_ass(tl, st)
    if "Dialogue:" in ass:
        (work / "studio.ass").write_text(ass, encoding="utf-8")
        g.append(f"[{cur}]ass=studio.ass[vass]")
    else:
        g.append(f"[{cur}]null[vass]")
    cur = "vass"
    # Emojis a color encima del subtítulo (PNG con transparencia: libass en Windows no los dibuja a color)
    if st.get("caption_emojis") and tl["captions"].get("enabled", True) and tl["words"] \
            and st["caption_style"] in ("karaoke", "simple"):
        from . import emoji as emoji_mod
        kq = OW / tl["width"]
        cap_px = int(st["caption_size"] * min(tl["width"], tl["height"]) / 1080)
        mv = caption_margin(tl, st, cap_px)
        size = _even(cap_px * 1.35 * kq)
        y = max(0, int((tl["height"] - mv - cap_px * 1.3) * kq) - size - int(12 * kq))
        for k, e in enumerate(emoji_mod.plan(group_words(tl["words"], int(st["caption_max_words"])))[:60]):
            png = emoji_mod.render_png(e["emoji"], size, work / f"emoji{k}.png")
            if not png:
                continue
            a, b = e["start"], max(e["end"], e["start"] + 0.6)
            idx = add(["-loop", "1", "-t", f"{b + 0.5:.3f}", "-i", png.name])
            g.append(f"[{idx}:v]format=rgba,fade=t=in:st={a:.3f}:d=0.12:alpha=1,"
                     f"fade=t=out:st={max(a, b - 0.15):.3f}:d=0.15:alpha=1[em{k}]")
            g.append(f"[{cur}][em{k}]overlay=x=(W-w)/2:y={y}:eof_action=pass:enable='between(t,{a:.3f},{b:.3f})'[emo{k}]")
            cur = f"emo{k}"
    g.append(f"[{cur}]null[vout]")

    # Música (con "ducking": baja cuando hablas)
    mus = []
    for k, m in enumerate(tl["tracks"]["music"]):
        a = by_id.get(m.get("asset_id"))
        if not a or not Path(a["path"]).exists():
            continue
        md = m["end"] - m["start"]
        idx = add(["-stream_loop", "-1", "-i", a["path"]])
        g.append(f"[{idx}:a]aformat=sample_rates=48000:channel_layouts=stereo,atrim=duration={md:.3f},"
                 f"asetpts=PTS-STARTPTS,volume={float(m.get('volume', st['music_volume'])):.3f},afade=t=in:d=0.6,"
                 f"afade=t=out:st={max(md - 1.2, 0):.3f}:d=1.2,adelay=delays={int(m['start'] * 1000)}:all=1[m{k}]")
        mus.append(f"[m{k}]")
    mix = ["[voicemix]"]
    if mus:
        g.append("".join(mus) + (f"amix=inputs={len(mus)}:normalize=0" if len(mus) > 1 else "anull") + "[music]")
        if st["duck_music"] and has_voice and stems and stems.get("vocals"):
            # ducking real: la clave es tu voz aislada, así la música no baja con los disparos del juego
            ki = add(["-i", str(stems["vocals"])])
            g.append("[voice]anull[voicemix]")
            g.append(f"[{ki}:a]aformat=sample_rates=48000:channel_layouts=stereo,volume=2[voicesc]")
            g.append("[music][voicesc]sidechaincompress=threshold=0.02:ratio=10:attack=15:release=380[musicd]")
            mix.append("[musicd]")
        elif st["duck_music"] and has_voice:
            g.append("[voice]asplit=2[voicemix][voicesc]")
            g.append("[music][voicesc]sidechaincompress=threshold=0.025:ratio=9:attack=20:release=450[musicd]")
            mix.append("[musicd]")
        else:
            g.append("[voice]anull[voicemix]")
            mix.append("[music]")
    else:
        g.append("[voice]anull[voicemix]")

    mix += broll_audio
    if ck_audio:
        mix.append(ck_audio)
    for k, s in enumerate(tl["tracks"]["sfx"]):
        a = by_id.get(s.get("asset_id"))
        if not a or not Path(a["path"]).exists():
            continue
        idx = add(["-i", a["path"]])
        g.append(f"[{idx}:a]aformat=sample_rates=48000:channel_layouts=stereo,"
                 f"volume={float(s.get('volume', 0.6)):.3f},adelay=delays={int(s['start'] * 1000)}:all=1[sx{k}]")
        mix.append(f"[sx{k}]")

    if len(mix) > 1:
        g.append("".join(mix) + f"amix=inputs={len(mix)}:normalize=0:duration=first:dropout_transition=0,"
                                "alimiter=limit=0.95[aout]")
    else:
        g.append("[voicemix]alimiter=limit=0.95[aout]")

    graph = work / "graph.txt"
    graph.write_text(";\n".join(g), encoding="utf-8")

    nv = gpu.nvenc_available()
    if quality == "preview":
        enc = (["-c:v", "h264_nvenc", "-preset", "p2", "-rc", "vbr", "-cq", "25", "-b:v", "0"] if nv
               else ["-c:v", "libx264", "-preset", "veryfast", "-crf", "25"])
    elif quality == "hd":
        enc = (["-c:v", "h264_nvenc", "-preset", "p3", "-rc", "vbr", "-cq", "22", "-b:v", "0"] if nv
               else ["-c:v", "libx264", "-preset", "veryfast", "-crf", "21"])
    else:  # máxima calidad: compresión casi sin pérdida visible, con margen de bitrate amplio
        enc = (["-c:v", "h264_nvenc", "-preset", "p7", "-tune", "hq", "-rc", "vbr", "-cq", "17", "-b:v", "0",
                "-maxrate", "120M", "-bufsize", "240M", "-spatial-aq", "1", "-temporal-aq", "1",
                "-rc-lookahead", "32", "-profile:v", "high"] if nv
               else ["-c:v", "libx264", "-preset", "slow", "-crf", "16", "-profile:v", "high"])
    cmd = ["ffmpeg", "-y", "-v", "error", "-progress", "pipe:1", "-nostats"]
    for args in inputs:
        cmd += args
    cmd += graph_flag(graph) + ["-map", "[vout]", "-map", "[aout]", "-t", f"{dur:.3f}", "-r", str(fps)]
    abr = "320k" if quality == "final" else "192k"
    cmd += enc + ["-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", abr, "-ar", "48000", "-movflags", "+faststart", str(out)]
    return cmd, dur, ("h264_nvenc" if nv else "libx264")


def stems_mod_public(info):
    from ..ai.stems import public
    return public(info)


def _sweep_orphan_previews() -> None:
    """Borra vistas previas viejas que ya no están en la base de datos (las que estaban en uso al reemplazarlas)."""
    with db.get_conn() as conn:
        known = {Path(r["path"]).name for r in conn.execute("SELECT path FROM studio_renders")}
    for f in config.STUDIO_RENDERS_DIR.glob("*_preview_*.mp4"):
        if f.name not in known:
            try:
                f.unlink()
            except OSError:
                pass


def render_to_file(p: dict, req_q: str, out: Path, work: Path, progress, qa_kind: str | None = None,
                   do_qa: bool = True) -> dict:
    """Renderiza un proyecto (o un clip armado como proyecto) a `out`: separación de voz si hace falta,
    FFmpeg, volumen -14 LUFS y control de calidad. Motor único para clips, YouTube, temas y Studio."""
    work.mkdir(parents=True, exist_ok=True)
    pre = None
    if do_qa:  # revisión preventiva: arregla lo seguro (textos colgados) en la copia que se renderiza
        from . import precheck
        try:
            tgt = ((p.get("source_meta") or {}).get("brief") or {}).get("target_duration")
            tl_fixed, pre = precheck.check(p, float(tgt) if isinstance(tgt, (int, float)) and tgt else None)
            p = {**p, "timeline": tl_fixed}
        except Exception as exc:  # noqa: BLE001 — la revisión nunca impide renderizar
            pre = {"issues": [], "fixed": [], "error": str(exc)[:160]}
    st_p, tl_p = p["style"], p["timeline"]
    mode = st_p.get("voice_mode", "original")
    stems_info = None
    need = (mode in ("realzar", "solo_voz") or (tl_p["tracks"]["music"] and st_p.get("duck_music"))) \
        and p["source_kind"] == "video" and p["source_meta"].get("has_audio")
    if need:
        from ..ai import stems as stems_mod
        try:
            stems_info = stems_mod.timeline_stems(
                Path(p["source_path"]), [(s["src_in"], s["src_out"]) for s in tl_p["tracks"]["video"]],
                work, mode if mode in stems_mod.VOICE_MODES else "original",
                progress=lambda x, m: progress(0.02 + 0.02 * x, m))
        except Exception as exc:  # noqa: BLE001 — sin separación el video sale con el audio original
            stems_info = {"error": str(exc)[:200]}
        if stems_info and stems_info.get("error"):
            stems_info = {"error": stems_info["error"]}
    good = stems_info if stems_info and not stems_info.get("error") else None
    cmd, dur, enc = build_command(p, req_q, work, out, stems=good)
    if len(subprocess.list2cmdline(cmd)) > 30000:  # demasiado larga para Windows: una sola entrada con split+trim
        cmd, dur, enc = build_command(p, req_q, work, out, force_single=True, stems=good)
    label = {"preview": "vista previa 720p", "hd": "vista previa HD 1080p"}.get(req_q, "video final")
    progress(0.04, f"Renderizando {label} ({'GPU' if enc == 'h264_nvenc' else 'CPU'})")
    errf = open(work / "ffmpeg.log", "w+", encoding="utf-8", errors="replace")
    proc = subprocess.Popen(cmd, cwd=work, stdout=subprocess.PIPE, stderr=errf,
                            text=True, encoding="utf-8", errors="replace")
    try:
        for line in proc.stdout:
            if line.startswith(("out_time_us=", "out_time_ms=")):
                try:
                    progress(0.04 + 0.94 * min(int(line.split("=")[1]) / 1e6 / max(dur, 0.1), 1.0))
                except ValueError:
                    pass
        proc.wait()
    except BaseException:
        proc.kill()
        errf.close()
        out.unlink(missing_ok=True)
        raise
    errf.seek(0)
    err = errf.read()
    errf.close()
    for f in work.iterdir():
        try:
            f.unlink()
        except OSError:
            pass
    try:
        work.rmdir()
    except OSError:
        pass
    if proc.returncode != 0 or not out.exists():
        out.unlink(missing_ok=True)
        detail = err.strip()[-500:] or f"FFmpeg se detuvo sin mensaje (código {proc.returncode}); ¿se cerró la app o la ventana?"
        raise RuntimeError(f"FFmpeg falló: {detail}")

    from ..editor import master, qa
    audio_rep, qa_rep = None, None
    if st_p.get("pro_audio", True) and p["source_meta"].get("has_audio", True):
        progress(0.985, "Ajustando el volumen (-14 LUFS)")
        audio_rep = master.normalize(out, "320k" if req_q == "final" else "192k")
    if do_qa and req_q != "preview":  # la revisión completa en HD y final (la vista previa prioriza velocidad)
        progress(0.99, "Revisando la calidad del video")
        kind = qa_kind or ("clip" if tl_p.get("height", 1920) > tl_p.get("width", 1080) and dur <= 90 else "video")
        qa_rep = qa.check(out, media.probe(out)["duration"], dur, kind=kind,
                          captions=bool((tl_p.get("captions") or {}).get("enabled")) and st_p.get("caption_style") != "none",
                          words=tl_p.get("words") or [], has_audio=bool(p["source_meta"].get("has_audio", True)))
    if pre and (pre.get("issues") or pre.get("fixed")):
        qa_rep = {**(qa_rep or {}), "precheck": pre}
    return {"encoder": enc, "duration": dur, "qa": qa_rep, "audio": audio_rep, "stems": stems_mod_public(stems_info)}


def run_studio_render(job: dict, progress) -> dict:
    params = job["params"] or {}
    pid, req_q = params["project_id"], params.get("quality", "preview")
    quality = "final" if req_q == "final" else "preview"  # "hd" es una vista previa a 1080p
    p = projects.get(pid)
    if not p:
        raise RuntimeError("El proyecto ya no existe.")
    if not Path(p["source_path"]).exists():
        raise RuntimeError("El archivo de tu toma ya no está disponible.")
    work = config.WORK_DIR / f"studio_render_{job['id']}"
    out = config.STUDIO_RENDERS_DIR / f"proyecto{pid}_{quality}_{int(time.time())}.mp4"
    progress(0.02, "Preparando el render")
    _sweep_orphan_previews()
    r = render_to_file(p, req_q, out, work, progress)
    m = media.probe(out)
    with db.get_conn() as conn:
        if quality == "preview":  # conservar solo la última vista previa
            for row in conn.execute("SELECT id, path FROM studio_renders WHERE project_id = ? AND quality = 'preview'",
                                    (pid,)).fetchall():
                try:  # en Windows falla si el reproductor aún tiene abierto el archivo: se borra en el próximo render
                    Path(row["path"]).unlink(missing_ok=True)
                except OSError:
                    pass
                conn.execute("DELETE FROM studio_renders WHERE id = ?", (row["id"],))
        cur = conn.execute(
            "INSERT INTO studio_renders (project_id, path, quality, duration, size_bytes, encoder, timeline_hash, qa) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (pid, str(out), quality, m["duration"], m["size_bytes"], r["encoder"], tlm.thash(p["timeline"], p["style"]),
             json.dumps({"qa": r["qa"], "audio": r["audio"], "stems": r["stems"]})))
    return {"render_id": cur.lastrowid, "quality": quality, "encoder": r["encoder"], "duration": m["duration"],
            "qa": r["qa"], "audio": r["audio"], "stems": r["stems"]}
