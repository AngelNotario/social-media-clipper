"""Pruebas del motor de IA y del editor.

Whisper y Ollama se sustituyen por dobles de prueba (no hay modelos en el entorno de CI);
FFmpeg (audio, escenas, render, subtítulos) se ejecuta de verdad.
"""
import json
import subprocess

import pytest

from conftest import make_video, needs_ffmpeg
from app.ai import highlights, llm, transcribe
from app.editor import subtitles

PHRASES = [
    "Hola a todos y bienvenidos al programa de hoy.",
    "¿Sabías que nadie te cuenta el verdadero secreto del dinero?",
    "La clave está en algo que aprendí después de perderlo todo.",
    "Durante tres años cometí el mismo error una y otra vez.",
    "Hasta que un día descubrí que el problema era mi forma de pensar.",
    "Eso cambió mi vida por completo y hoy te lo voy a explicar.",
    "Primero tienes que entender cómo funciona el interés compuesto.",
    "Es increíble lo que pasa cuando dejas que el tiempo trabaje por ti.",
    "Muchos creen que necesitas millones para empezar, pero es mentira.",
    "Con poco dinero y paciencia los resultados llegan.",
    "Bueno, eso es todo por hoy, gracias por vernos.",
]


def fake_segments(seconds_per=6.0):
    segs, t = [], 0.0
    for p in PHRASES:
        words, words_t = p.split(), t
        step = (seconds_per - 0.4) / len(words)
        ws = []
        for w in words:
            ws.append({"start": round(words_t, 2), "end": round(words_t + step * 0.9, 2), "word": w})
            words_t += step
        segs.append({"start": round(t, 2), "end": round(t + seconds_per - 0.4, 2), "text": p, "words": ws})
        t += seconds_per
    return segs


@pytest.fixture()
def fake_whisper(monkeypatch):
    calls = []

    def _fake(wav, model_name="x", language="es", duration=None, progress=lambda p: None):
        calls.append(str(wav))
        progress(1.0)
        return {"language": "es", "model": "fake", "device": "cpu", "segments": fake_segments()}

    monkeypatch.setattr(transcribe, "transcribe", _fake)
    return calls


@pytest.fixture()
def long_video(dirs):
    return make_video(dirs["videos"] / "podcast_ia.mp4", w=1280, h=720, seconds=66)


def _import(client, path):
    r = client.post("/api/library/import", json={"paths": [str(path)]}).json()
    if r["imported"]:
        return r["results"][0]["video"]["id"]
    return r["results"][0]["video_id"]


# ---------- unidades ----------

def test_snap_respects_sentences_and_durations():
    segs = fake_segments()
    s, e = highlights.snap(6.3, 20.0, segs, min_d=15, max_d=30, duration=66)
    assert abs(s - (6.0 - 0.15)) < 0.01           # empieza al inicio de una frase
    assert any(abs(e - (x["end"] + 0.35)) < 0.01 for x in segs)  # termina al final de una frase
    assert 15 <= e - s <= 30.6


def test_snap_extends_short_ranges():
    segs = fake_segments()
    s, e = highlights.snap(12.0, 14.0, segs, min_d=20, max_d=40, duration=66)
    assert e - s >= 20


def test_dedupe_removes_overlaps():
    c = [{"start": 0, "end": 30, "score": 80}, {"start": 5, "end": 35, "score": 70},
         {"start": 40, "end": 60, "score": 60}]
    kept = highlights.dedupe(c)
    assert [k["score"] for k in kept] == [80, 60]


def test_subtitles_ass_karaoke():
    words = subtitles.clip_words(fake_segments(), 6.0, 17.6)
    assert words[0]["start"] == 0.0
    ass = subtitles.build_ass(words, 11.6, "karaoke", "bottom", title="El secreto del dinero")
    assert "PlayResY: 1920" in ass
    assert "EL SECRETO DEL DINERO" in ass
    assert "\\c&H00E5FF&" in ass  # palabra activa resaltada
    dialogues = [l for l in ass.splitlines() if l.startswith("Dialogue: 0")]
    assert len(dialogues) == len(words)


# ---------- flujo completo ----------

@needs_ffmpeg
def test_analyze_with_llm_and_render(client, long_video, fake_whisper, monkeypatch):
    monkeypatch.setattr(llm, "status", lambda url: {"available": True, "models": ["qwen3:14b"]})
    sent = []

    def fake_chat(url, model, system, user, schema, **kw):
        sent.append(user)
        assert "[6.0-11.6]" in user  # la IA recibe la transcripción con tiempos
        return {"moments": [
            {"start": 6.0, "end": 29.6, "title": "El secreto del dinero", "hook": "Nadie te cuenta esto",
             "reason": "Abre con una pregunta", "suggestions": "Texto en pantalla",
             "scores": {"hook": 90, "contexto": 80, "emocion": 75, "valor": 85, "narrativa": 70, "retencion": 88}},
            {"start": 36.0, "end": 59.6, "title": "Interés compuesto", "hook": "Es increíble",
             "reason": "Dato útil", "suggestions": "",
             "scores": {"hook": 60, "contexto": 70, "emocion": 50, "valor": 80, "narrativa": 50, "retencion": 60}},
            {"start": 8.0, "end": 28.0, "title": "Duplicado", "hook": "", "reason": "", "suggestions": "",
             "scores": {"hook": 10, "contexto": 10, "emocion": 10, "valor": 10, "narrativa": 10, "retencion": 10}},
        ]}

    monkeypatch.setattr(llm, "chat_json", fake_chat)
    vid = _import(client, long_video)

    job = client.post(f"/api/videos/{vid}/analyze", json={"max_clips": 5, "min_dur": 15, "max_dur": 30}).json()
    job = client.get(f"/api/jobs/{job['id']}").json()
    assert job["status"] == "done", job
    assert job["result"]["method"] == "ai" and job["result"]["clips"] == 2  # el duplicado se descarta
    assert sent

    a = client.get(f"/api/videos/{vid}/analysis").json()
    assert a["analysis"]["method"] == "ai" and a["has_transcript"]
    assert len(client.get(f"/api/videos/{vid}/transcript").json()["segments"]) == len(PHRASES)

    clips = client.get(f"/api/videos/{vid}/clips").json()
    assert clips[0]["title"] == "El secreto del dinero"
    assert clips[0]["score"] > clips[1]["score"]
    assert "secreto" in clips[0]["text"]
    assert 15 <= clips[0]["duration"] <= 30.6
    assert set(clips[0]["scores"]) >= {"hook", "retencion", "energia"}
    cid = clips[0]["id"]

    # Revisión: ajustar tiempos, aprobar, notas; rango inválido rechazado
    assert client.patch(f"/api/clips/{cid}", json={"start_s": 50, "end_s": 40}).status_code == 400
    upd = client.patch(f"/api/clips/{cid}", json={"start_s": 6.5, "status": "approved", "notes": "Buen gancho"}).json()
    assert upd["status"] == "approved" and upd["start_s"] == 6.5

    # Render vertical con subtítulos y título
    rj = client.post(f"/api/clips/{cid}/render", json={
        "crop_mode": "face", "subtitles": True, "sub_style": "karaoke", "show_title": True}).json()
    rj = client.get(f"/api/jobs/{rj['id']}").json()
    assert rj["status"] == "done", rj
    assert rj["result"]["crop_used"] == "center"  # el video de prueba no tiene rostros
    assert rj["result"]["subtitled_words"] > 10

    renders = client.get("/api/renders").json()
    r = renders[0]
    probe = json.loads(subprocess.run(
        ["ffprobe", "-v", "error", "-show_streams", "-of", "json", r["path"]],
        capture_output=True, text=True).stdout)
    vstream = next(s for s in probe["streams"] if s["codec_type"] == "video")
    assert (vstream["width"], vstream["height"]) == (1080, 1920)
    assert any(s["codec_type"] == "audio" for s in probe["streams"])
    assert abs(r["duration"] - (upd["end_s"] - 6.5)) < 0.3

    part = client.get(r["url"], headers={"Range": "bytes=0-1023"})
    assert part.status_code == 206 and len(part.content) == 1024
    assert client.get(r["url"] + "?download=true").headers["content-disposition"].startswith("attachment")

    # Re-analizar conserva el clip aprobado y reemplaza los demás
    client.post(f"/api/videos/{vid}/analyze", json={"max_clips": 5, "min_dur": 15, "max_dur": 30})
    clips2 = client.get(f"/api/videos/{vid}/clips").json()
    assert any(c["id"] == cid and c["status"] == "approved" for c in clips2)
    assert len(fake_whisper) == 1  # la transcripción se reutiliza

    # Quitar el video borra clips y exportaciones, nunca el original
    path = r["path"]
    client.delete(f"/api/library/{vid}")
    import os
    assert not os.path.exists(path)
    assert long_video.exists()
    assert client.get("/api/renders").json() == []


@needs_ffmpeg
def test_analyze_falls_back_to_heuristic(client, long_video, fake_whisper, monkeypatch):
    monkeypatch.setattr(llm, "status", lambda url: {"available": False, "models": []})
    vid = _import(client, long_video)
    job = client.post(f"/api/videos/{vid}/analyze", json={"max_clips": 3, "min_dur": 15, "max_dur": 30}).json()
    job = client.get(f"/api/jobs/{job['id']}").json()
    assert job["status"] == "done" and job["result"]["method"] == "heuristic"
    clips = client.get(f"/api/videos/{vid}/clips").json()
    assert 1 <= len(clips) <= 3
    assert all(c["source"] == "heuristic" for c in clips)
    assert "hook" not in clips[0]["scores"]  # no se fingen criterios de IA
    client.delete(f"/api/library/{vid}")


@needs_ffmpeg
def test_manual_clip_and_blur_render(client, long_video, fake_whisper):
    vid = _import(client, long_video)
    c = client.post("/api/clips", json={"video_id": vid, "start_s": 10, "end_s": 18, "title": "Mío"}).json()
    assert c["source"] == "manual"
    rj = client.post(f"/api/clips/{c['id']}/render", json={"crop_mode": "blur", "subtitles": False}).json()
    assert client.get(f"/api/jobs/{rj['id']}").json()["status"] == "done"
    assert client.delete(f"/api/clips/{c['id']}").json() == {"ok": True}
    client.delete(f"/api/library/{vid}")


def test_ai_status(client):
    st = client.get("/api/ai/status").json()
    assert st["ollama"]["model"] == "qwen3:14b"
    assert st["whisper"]["model"] == "large-v3-turbo"
    assert "hook" in st["criteria"]
