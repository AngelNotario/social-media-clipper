"""Render de clips verticales 9:16 con FFmpeg."""
from __future__ import annotations

import json
import re
from pathlib import Path

from .. import config, db, media
from ..ai import gpu
from . import crop

OUT_W, OUT_H = 1080, 1920
CROP_MODES = ("face", "center", "blur", "fit", "stream")


def _even(n: float) -> int:
    return int(n) // 2 * 2


def video_filter(mode: str, w: int, h: int, face_x: float | None, track=None, t0: float = 0.0) -> str:
    target = 9 / 16
    if mode in ("face", "center"):
        if w / h > target:  # fuente más ancha que 9:16 → recortar a los lados
            cw, ch = _even(h * target), _even(h)
            cx = (face_x if (mode == "face" and face_x is not None) else 0.5) * w
            x = str(int(min(max(cx - cw / 2, 0), w - cw)))
            if mode == "face" and track:  # el encuadre sigue al rostro (cambios de hablante incluidos)
                x = "'" + crop.track_expr(track, cw, w, t0=t0) + "'"
            return f"[0:v]crop={cw}:{ch}:{x}:0,scale={OUT_W}:{OUT_H},setsar=1[v0]"
        cw, ch = _even(w), _even(w / target)
        y = int((h - ch) / 2)
        return f"[0:v]crop={cw}:{ch}:0:{max(y, 0)},scale={OUT_W}:{OUT_H},setsar=1[v0]"
    if mode == "blur":
        return (f"[0:v]split[a][b];"
                f"[a]scale={OUT_W}:{OUT_H}:force_original_aspect_ratio=increase,crop={OUT_W}:{OUT_H},"
                f"boxblur=24:4,eq=brightness=-0.08[bg];"
                f"[b]scale={OUT_W}:{OUT_H}:force_original_aspect_ratio=decrease[fg];"
                f"[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1[v0]")
    # fit: barras negras
    return (f"[0:v]scale={OUT_W}:{OUT_H}:force_original_aspect_ratio=decrease,"
            f"pad={OUT_W}:{OUT_H}:(ow-iw)/2:(oh-ih)/2:black,setsar=1[v0]")


def encoder_args() -> tuple[list[str], str]:
    if gpu.nvenc_available():
        return ["-c:v", "h264_nvenc", "-preset", "p7", "-tune", "hq", "-rc", "vbr", "-cq", "17", "-b:v", "0",
                "-maxrate", "80M", "-bufsize", "160M", "-spatial-aq", "1", "-temporal-aq", "1",
                "-rc-lookahead", "32", "-profile:v", "high"], "h264_nvenc"
    return ["-c:v", "libx264", "-preset", "slow", "-crf", "16", "-profile:v", "high"], "libx264"


def _slug(text: str) -> str:
    """Nombre de archivo ASCII (sin acentos ni ñ) para evitar problemas en Windows y otras apps."""
    import unicodedata
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    s = re.sub(r"[^\w\s-]", "", ascii_text).strip().lower()
    return re.sub(r"[\s_-]+", "-", s)[:50] or "clip"


def run_render(job: dict, progress) -> dict:
    """Genera el clip con el motor único del Studio (mismo resultado que verlo y editarlo en el Studio)."""
    from ..studio import clip_project
    from ..studio.probe import probe_any
    from ..studio.render import render_to_file
    opts = dict(job["params"] or {})
    if opts.get("crop_mode") not in CROP_MODES:
        opts["crop_mode"] = "face"
    with db.get_conn() as conn:
        clip = conn.execute("SELECT * FROM clips WHERE id = ?", (job["clip_id"],)).fetchone()
        if not clip:
            raise RuntimeError("El clip ya no existe.")
    try:
        tl, st, v, info = clip_project.build(clip["id"], opts, progress)
    except ValueError as exc:
        raise RuntimeError(str(exc)) from exc
    out_dir = config.EXPORTS_DIR / f"video_{v['id']}"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"clip{clip['id']}_{_slug(clip['title'])}_{job['id']}.mp4"
    p = {"source_path": v["stored_path"], "source_kind": "video", "source_meta": probe_any(Path(v["stored_path"])),
         "timeline": tl, "style": st}
    r = render_to_file(p, "final", out, config.WORK_DIR / f"render_{job['id']}", progress, qa_kind="clip",
                       do_qa=opts.get("qa", True))
    meta = media.probe(out)
    gen = info.get("generative") or {}
    used = {**opts, "director": info["director"], "audio": r["audio"], "qa": r["qa"], "stems": r["stems"],
            "crop_used": info["crop_used"],
            "generative": {k: gen.get(k) for k in ("score", "found", "planned", "web", "reused", "error", "skipped")
                           if gen.get(k) is not None} or None}
    with db.get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO renders (clip_id, path, options, duration, size_bytes, encoder) VALUES (?, ?, ?, ?, ?, ?)",
            (clip["id"], str(out), json.dumps(used), meta["duration"], meta["size_bytes"], r["encoder"]),
        )
    return {"render_id": cur.lastrowid, "encoder": r["encoder"], "crop_used": info["crop_used"],
            "subtitled_words": info["words"], "director": info["director"], "audio": r["audio"], "qa": r["qa"],
            "stems": r["stems"], "generative": used["generative"]}
