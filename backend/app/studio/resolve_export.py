"""Exportar un proyecto del Studio a DaVinci Resolve (FCPXML 1.9 + subtítulos SRT).

Resolve importa FCPXML desde File → Import → Timeline. Lo que se traslada:
- V1: tu toma con todos los cortes (cada toma con su punto de entrada/salida en el archivo original) y su zoom.
- V2: recursos (imágenes, clips, b-roll), con tamaño y posición de tarjeta / ventana pequeña.
- V3: textos (títulos, rótulos, memes) como títulos de texto editables.
- A1: el audio de tu toma (va con el video). A2: música. A3: efectos de sonido, con su volumen.
- subtitulos.srt: tus subtítulos (File → Import → Subtitle) para editarlos en Resolve.
Los archivos se enlazan desde su ubicación en tu PC (no se copian). Lo que es propio del render de la app
(pantalla verde, voz con IA, animaciones de tarjetas, golpes de zoom) se rehace en Resolve si lo necesitas.
"""
from __future__ import annotations

import io
import zipfile
from pathlib import Path
from urllib.parse import quote
from xml.sax.saxutils import escape

from . import assets


def _uri(path: str) -> str:
    p = Path(path).resolve().as_posix()
    return "file:///" + quote(p.lstrip("/"), safe="/:")


class _T:
    """Tiempos racionales en cuadros (Resolve exige tiempos alineados al cuadro)."""

    def __init__(self, fps: int):
        self.fps = fps

    def __call__(self, sec: float) -> str:
        return f"{max(0, round(float(sec) * self.fps))}/{self.fps}s"


def _srt_time(t: float) -> str:
    ms = int(round(max(0.0, t) * 1000))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def captions_srt(words: list[dict], max_words: int = 6) -> str:
    """Subtítulos en frases cortas (corta en pausas, puntuación o cada `max_words` palabras)."""
    out, cur = [], []
    for w in words:
        if cur and (len(cur) >= max_words or w["start"] - cur[-1]["end"] > 0.6):
            out.append(cur)
            cur = []
        cur.append(w)
        if w["word"].rstrip().endswith((".", "?", "!")):
            out.append(cur)
            cur = []
    if cur:
        out.append(cur)
    lines = []
    for i, g in enumerate(out, 1):
        lines.append(f"{i}\n{_srt_time(g[0]['start'])} --> {_srt_time(g[-1]['end'])}\n"
                     f"{' '.join(x['word'].strip() for x in g)}\n")
    return "\n".join(lines)


def build_fcpxml(p: dict) -> str:
    tl, meta = p["timeline"], p["source_meta"]
    W, H = int(tl["width"]), int(tl["height"])
    fps = 60 if float(meta.get("fps") or 0) >= 50 else int(tl.get("fps") or 30)
    T = _T(fps)
    segs = tl["tracks"]["video"]
    dur = float(tl["duration"])
    by_id = {a["id"]: a for a in assets.list_assets()}

    res, rid = [], [1]

    def new_id() -> str:
        rid[0] += 1
        return f"r{rid[0]}"

    res.append(f'<format id="r1" name="SMC_{W}x{H}_{fps}" frameDuration="1/{fps}s" width="{W}" height="{H}"/>')
    src_id = new_id()
    src_dur = float(meta.get("duration") or max((s["src_out"] for s in segs), default=dur))
    has_v = 1 if p.get("source_kind") == "video" and meta.get("has_video", True) else 0
    has_a = 1 if meta.get("has_audio", True) else 0
    res.append(f'<asset id="{src_id}" name="{escape(Path(p["source_path"]).stem)}" start="0s" '
               f'duration="{T(src_dur)}" hasVideo="{has_v}" hasAudio="{has_a}" format="r1" audioSources="1" '
               f'audioChannels="2" audioRate="48000"><media-rep kind="original-media" '
               f'src="{escape(_uri(p["source_path"]))}"/></asset>')

    asset_ref: dict[int, str] = {}

    def ref_for(aid) -> tuple[str, dict] | tuple[None, None]:
        a = by_id.get(aid)
        if not a or not a.get("available"):
            return None, None
        if aid not in asset_ref:
            i = new_id()
            asset_ref[aid] = i
            is_img = a["media_type"] == "image"
            hv = 0 if a["media_type"] == "audio" else 1
            ha = 1 if a["media_type"] in ("audio", "video") else 0
            d = "0s" if is_img else T(float(a.get("duration") or 3600))
            fmt = ' format="r1"' if hv else ""
            res.append(f'<asset id="{i}" name="{escape(a["title"])}" start="0s" duration="{d}" hasVideo="{hv}" '
                       f'hasAudio="{ha}"{fmt} audioSources="{ha}" audioChannels="2" '
                       f'audioRate="48000"><media-rep kind="original-media" src="{escape(_uri(a["path"]))}"/></asset>')
        return asset_ref[aid], by_id[aid]

    title_fx = new_id()
    res.append(f'<effect id="{title_fx}" name="Basic Title" '
               'uid=".../Titles.localized/Bumper:Opener.localized/Basic Title.localized/Basic Title.moti"/>')

    # Elementos conectados: cada uno cuelga de la toma del spine que está debajo de su inicio
    attached: dict[int, list[str]] = {i: [] for i in range(len(segs))}

    def host(t: float) -> int:
        for i, s in enumerate(segs):
            if s["start"] <= t < s["start"] + (s["src_out"] - s["src_in"]):
                return i
        return max(0, len(segs) - 1)

    def local(i: int, t: float) -> str:
        s = segs[i]
        return T(s["src_in"] + (t - s["start"]))

    ts_n = [0]
    for b in tl["tracks"]["broll"]:
        r, a = ref_for(b.get("asset_id"))
        if not r:
            continue
        t0, t1 = float(b["start"]), float(b["end"])
        i = host(t0)
        mode = b.get("mode") or "full"
        tf = ""
        if mode in ("card", "pip"):
            sc = float(b.get("scale") or 0.4)
            pos = b.get("pos") or {"x": 0.5, "y": 0.5}
            # FCPXML: posición en % de la altura del cuadro desde el centro
            px = (float(pos.get("x", 0.5)) - 0.5) * (W / H) * 100
            py = (0.5 - float(pos.get("y", 0.5))) * 100
            tf = f'<adjust-transform position="{px:.2f} {py:.2f}" scale="{sc:.3f} {sc:.3f}"/>'
        name = escape(b.get("label") or a["title"])
        if a["media_type"] == "image":
            attached[i].append(f'<video ref="{r}" lane="1" offset="{local(i, t0)}" name="{name}" start="0s" '
                               f'duration="{T(t1 - t0)}">{tf}</video>')
        else:
            vol = float(b.get("volume") or 0)
            av = '' if vol > 0 else '<adjust-volume amount="-96dB"/>'
            attached[i].append(f'<asset-clip ref="{r}" lane="1" offset="{local(i, t0)}" name="{name}" '
                               f'start="{T(b.get("src_in") or 0)}" duration="{T(t1 - t0)}">{tf}{av}</asset-clip>')

    for t in tl["tracks"]["text"]:
        t0, t1 = float(t["start"]), float(t["end"])
        i = host(t0)
        ts_n[0] += 1
        tsid = f"ts{ts_n[0]}"
        size = 90 if t.get("kind") in ("title", "meme") else 60
        attached[i].append(
            f'<title ref="{title_fx}" lane="2" offset="{local(i, t0)}" name="{escape(t["text"][:40])}" '
            f'start="0s" duration="{T(max(0.2, t1 - t0))}"><text><text-style ref="{tsid}">{escape(t["text"])}'
            f'</text-style></text><text-style-def id="{tsid}"><text-style font="Arial Black" fontSize="{size}" '
            f'fontColor="1 1 1 1" bold="1" alignment="center" strokeColor="0 0 0 1" strokeWidth="-3"/>'
            f'</text-style-def></title>')

    def audio(item: dict, lane: int, t1: float | None) -> None:
        r, a = ref_for(item.get("asset_id"))
        if not r:
            return
        t0 = float(item["start"])
        end = t1 if t1 is not None else t0 + float(a.get("duration") or 2)
        end = min(end, dur)
        if end - t0 < 0.05:
            return
        i = host(t0)
        vol = float(item.get("volume") if item.get("volume") is not None else 1.0)
        db_ = -96.0 if vol <= 0.001 else 20 * __import__("math").log10(vol)
        attached[i].append(f'<asset-clip ref="{r}" lane="{lane}" offset="{local(i, t0)}" name="{escape(a["title"])}" '
                           f'start="0s" duration="{T(end - t0)}"><adjust-volume amount="{db_:.1f}dB"/></asset-clip>')

    for m in tl["tracks"]["music"]:
        audio(m, -1, float(m.get("end") or dur))
    for s in tl["tracks"]["sfx"]:
        audio(s, -2, None)

    spine = []
    for i, s in enumerate(segs):
        z = float(s.get("zoom") or 1.0)
        tf = f'<adjust-transform scale="{z:.3f} {z:.3f}"/>' if z > 1.001 else ""
        spine.append(f'<asset-clip ref="{src_id}" offset="{T(s["start"])}" name="Toma {i + 1}" '
                     f'start="{T(s["src_in"])}" duration="{T(s["src_out"] - s["src_in"])}" format="r1" '
                     f'tcFormat="NDF">{tf}{"".join(attached[i])}</asset-clip>')
    name = escape(p.get("name") or "Proyecto SMC")
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE fcpxml>\n<fcpxml version="1.9">\n'
        f'<resources>{"".join(res)}</resources>\n'
        f'<library><event name="Social Media Clipper"><project name="{name}">'
        f'<sequence format="r1" duration="{T(dur)}" tcStart="0s" tcFormat="NDF" audioLayout="stereo" '
        f'audioRate="48k"><spine>{"".join(spine)}</spine></sequence></project></event></library>\n</fcpxml>\n'
    )


README = """Cómo abrir este proyecto en DaVinci Resolve
==========================================

1. Abre DaVinci Resolve y crea (o abre) un proyecto.
2. File (Archivo) → Import → Timeline… y elige «{name}.fcpxml».
   Deja marcada la opción de importar los medios automáticamente.
3. Subtítulos: File → Import → Subtitle… y elige «subtitulos.srt»; arrástralo a la pista de subtítulos.
4. Si Resolve dice que un archivo está «offline», haz clic derecho → Relink y busca la carpeta del archivo.

Qué trae: tu toma con todos los cortes y zooms (V1), recursos e imágenes con su tamaño y posición (V2),
textos editables (V3), música y efectos con su volumen (A2/A3).
Qué no trae: la pantalla verde ya quitada, la voz con IA, las animaciones de tarjetas y los golpes de zoom;
eso se rehace en Resolve si lo quieres.
Configuración recomendada de exportación en Resolve para YouTube: 2560×1440, {fps} fps, H.264/H.265 calidad alta.
"""


def build_zip(p: dict) -> tuple[bytes, str]:
    xml = build_fcpxml(p)
    name = "".join(c if c.isalnum() or c in " -_" else "_" for c in (p.get("name") or "proyecto")).strip() or "proyecto"
    fps = 60 if float(p["source_meta"].get("fps") or 0) >= 50 else int(p["timeline"].get("fps") or 30)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(f"{name}.fcpxml", xml)
        words = p["timeline"].get("words") or []
        if words:
            z.writestr("subtitulos.srt", captions_srt(words))
        z.writestr("LEEME.txt", README.format(name=name, fps=fps))
    return buf.getvalue(), name
