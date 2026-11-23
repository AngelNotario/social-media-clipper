"""Trabajo 'analyze': audio → transcripción → energía/escenas → mejores momentos → clips candidatos."""
from __future__ import annotations

import json
from pathlib import Path

from .. import config, db
from . import audio, highlights, transcribe


def load_transcript(video_id: int) -> dict | None:
    with db.get_conn() as conn:
        row = conn.execute("SELECT * FROM transcripts WHERE video_id = ?", (video_id,)).fetchone()
    if not row:
        return None
    d = dict(row)
    d["segments"] = json.loads(d["segments"])
    from . import vocab  # diccionario de nombres también en los clips («Neva» → «Eneba»)
    for s in d["segments"]:
        if s.get("words"):
            s["words"] = vocab.apply(s["words"])
            s["text"] = " ".join(w["word"] for w in s["words"])
    return d


def run_analyze(job: dict, progress) -> dict:
    params = job["params"] or {}
    with db.get_conn() as conn:
        v = conn.execute("SELECT * FROM videos WHERE id = ?", (job["video_id"],)).fetchone()
    if not v:
        raise RuntimeError("El video ya no existe en la biblioteca.")
    src = Path(v["stored_path"])
    if not src.exists():
        raise RuntimeError("El archivo de video no se encuentra en su ubicación.")
    if not v["has_audio"]:
        raise RuntimeError("El video no tiene audio: no se puede transcribir.")
    duration = v["duration"] or 0

    cfg = {
        "max_clips": int(params.get("max_clips", 10)),
        "min_dur": float(params.get("min_dur", 20)),
        "max_dur": float(params.get("max_dur", 60)),
        "mode": params.get("mode", "speech"),  # speech | stream
    }
    from .. import knowledge
    llm_cfg = {"url": db.get_setting("ollama_url"), "model": db.get_setting("ollama_model"),
               "context": knowledge.prompt_context("clip", params.get("style_id"), max_chars=3000)}
    if params.get("judge", True):  # juez de gancho y retención (modelo distinto si hay otro instalado)
        from . import judge
        jm = judge.pick_model(llm_cfg["url"], llm_cfg["model"], (params.get("models") or {}).get("judge")
                              or params.get("judge_model") or db.get_setting("judge_model"))
        llm_cfg["judge"] = {"url": llm_cfg["url"], "model": jm,
                            "context": knowledge.prompt_context("clip", params.get("style_id"), max_chars=1500)}

    wav = config.WORK_DIR / f"video_{v['id']}.wav"
    voice_wav = config.WORK_DIR / f"video_{v['id']}_voz.wav"
    from .. import stream as stream_mod
    vtrack = stream_mod.voice_track(v["id"], src)
    try:
        progress(0.01, "Extrayendo audio")
        audio.extract_wav(src, wav)  # mezcla completa (energía)
        if vtrack:
            audio.extract_wav(src, voice_wav, vtrack)  # pista de micrófono (transcripción)

        tr = None if params.get("retranscribe") else load_transcript(v["id"])
        if tr is None:
            progress(0.04, "Transcribiendo con Whisper")
            result = transcribe.transcribe(
                voice_wav if vtrack else wav, db.get_setting("whisper_model"), db.get_setting("whisper_language"), duration,
                lambda p: progress(0.04 + 0.56 * p, f"Transcribiendo con Whisper ({int(p * 100)}%)"),
            )
            with db.get_conn() as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO transcripts (video_id, language, model, device, segments) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (v["id"], result["language"], result["model"], result["device"],
                     json.dumps(result["segments"], ensure_ascii=False)),
                )
            tr = load_transcript(v["id"])
        segments = tr["segments"]
        if not segments and cfg["mode"] != "stream":
            raise RuntimeError("Whisper no detectó voz en el video. Si es un gameplay sin voz, usa el modo stream.")

        progress(0.62, "Midiendo energía del audio")
        energy = audio.energy_curve(wav)
        scenes: list[float] = []
        if duration <= 1800:  # en streams largos se omite (tardaría demasiado y aporta poco)
            progress(0.66, "Detectando cortes de escena")
            scenes = audio.detect_scenes(src)

        progress(0.70, "Buscando los mejores momentos")
        moments, method = highlights.find_highlights(
            segments, duration, energy, scenes, cfg, llm_cfg,
            lambda p, s: progress(0.70 + 0.28 * p, s),
        )

        with db.get_conn() as conn:
            # Se reemplazan los candidatos automáticos pendientes o descartados;
            # se conservan los aprobados y los creados a mano.
            conn.execute(
                "DELETE FROM clips WHERE video_id = ? AND source != 'manual' AND status != 'approved'",
                (v["id"],),
            )
            for m in moments:
                conn.execute(
                    "INSERT INTO clips (video_id, start_s, end_s, title, hook, reason, suggestions, scores, score, source, judge) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (v["id"], m["start"], m["end"], m["title"], m["hook"], m["reason"], m["suggestions"],
                     json.dumps(m["scores"]), m["score"], m["source"],
                     json.dumps(m["judge"], ensure_ascii=False) if m.get("judge") else None),
                )
            conn.execute(
                "INSERT OR REPLACE INTO analyses (video_id, method, llm_model, scenes, energy, params) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (v["id"], method, llm_cfg["model"] if "ai" in method else None,
                 json.dumps(scenes), json.dumps(energy), json.dumps(cfg)),
            )
        jinfo = llm_cfg.get("judge") or {}
        return {"judge_model": jinfo.get("model"), "clips": len(moments), "method": method, "model": llm_cfg["model"] if "ai" in method else None, "segments": len(segments),
                "scenes": len(scenes), "language": tr["language"]}
    finally:
        wav.unlink(missing_ok=True)
        voice_wav.unlink(missing_ok=True)
