"""Pruebas del modo stream: pistas de OBS, cámara manual, encuadre dividido, clips y resumen para YouTube.
Whisper y la IA se simulan; FFmpeg es real."""
import json
import subprocess

import pytest

from conftest import needs_ffmpeg
from app import stream
from app.ai import audio, llm, transcribe


def make_stream(path, seconds=250, tracks=2):
    """Video tipo OBS 640x360 con 2 pistas de audio (mezcla + micrófono)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    cmd = ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", f"testsrc=size=640x360:rate=15:duration={seconds}"]
    for k in range(tracks):
        cmd += ["-f", "lavfi", "-i", f"sine=frequency={300 + 200 * k}:duration={seconds}"]
    cmd += ["-map", "0:v"] + sum((["-map", f"{k + 1}:a"] for k in range(tracks)), [])
    cmd += ["-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(path)]
    subprocess.run(cmd, check=True)
    return path


def _segments(seconds):
    segs, t, n = [], 0.0, 0
    while t + 5 < seconds:
        words = f"frase numero {n} del stream muy emocionante".split()
        ws = [{"start": round(t + i * 0.6, 2), "end": round(t + i * 0.6 + 0.5, 2), "word": w} for i, w in enumerate(words)]
        segs.append({"start": t, "end": round(t + 4.6, 2), "text": " ".join(words), "words": ws})
        t += 6
        n += 1
    return segs


@pytest.fixture()
def stream_video(client, dirs):
    src = make_stream(dirs["videos"] / "stream" / "stream_obs.mp4")
    r = client.post("/api/library/import", json={"paths": [str(src)]}).json()
    vid = r["results"][0]["video"]["id"] if r["imported"] else r["results"][0]["video_id"]
    yield vid, src
    client.delete(f"/api/library/{vid}")


def test_split_filter_geometry():
    f = stream.split_filter(1920, 1080, {"x": 0.75, "y": 0.7, "w": 0.25, "h": 0.3})
    assert "crop=480:324:1440:756" in f
    assert "vstack" in f and f.endswith("[v0]")


@needs_ffmpeg
def test_stream_settings_tracks_and_split_clip(client, stream_video, monkeypatch):
    vid, src = stream_video
    assert audio.audio_tracks(src) == 2
    st = client.get(f"/api/stream/videos/{vid}").json()
    assert st["audio_tracks"] == 2 and st["auto_voice_track"] == 1 and st["facecam"] is None

    # La cámara marcada a mano + modo "siempre"
    client.put(f"/api/stream/videos/{vid}", json={"facecam": {"x": 0.7, "y": 0.65, "w": 0.28, "h": 0.33},
                                                  "cam_mode": "always"})
    assert client.get(f"/api/stream/videos/{vid}").json()["facecam"]["x"] == 0.7
    assert client.get(f"/api/stream/videos/{vid}/frame?t=5").headers["content-type"] == "image/jpeg"

    # Clips en modo stream: se transcribe la pista del micrófono (2.ª pista)
    seen = []

    def fake_tr(wav, model_name="x", language="es", duration=None, progress=lambda p: None):
        seen.append(str(wav))
        return {"language": "es", "model": "fake", "device": "cpu", "segments": _segments(250)}
    monkeypatch.setattr(transcribe, "transcribe", fake_tr)
    monkeypatch.setattr(llm, "status", lambda url: {"available": False, "models": []})
    j = client.post(f"/api/stream/videos/{vid}/clips", json={"max_clips": 3, "min_dur": 15, "max_dur": 30}).json()
    j = client.get(f"/api/jobs/{j['id']}").json()
    assert j["status"] == "done", j
    assert any("voz" in s or "voice" in s for s in seen)
    clips = client.get(f"/api/videos/{vid}/clips").json()
    assert clips

    r = client.post(f"/api/clips/{clips[0]['id']}/render", json={"crop_mode": "stream", "subtitles": True}).json()
    r = client.get(f"/api/jobs/{r['id']}").json()
    assert r["status"] == "done", r
    assert "cámara" in r["result"]["crop_used"]
    from app import db
    with db.get_conn() as conn:
        out = conn.execute("SELECT path FROM renders WHERE id = ?", (r["result"]["render_id"],)).fetchone()[0]
    info = json.loads(subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                                      "stream=width,height", "-of", "json", out], capture_output=True, text=True).stdout)
    assert info["streams"][0]["width"] == 1080 and info["streams"][0]["height"] == 1920

    # Sin cámara: el mismo clip usa el juego completo
    client.put(f"/api/stream/videos/{vid}", json={"cam_mode": "never"})
    filt, used = stream.stream_layout(client.get(f"/api/library/{vid}").json(), 0, 10)
    assert "sin cámara" in used and "vstack" not in filt


@needs_ffmpeg
def test_youtube_summary(client, stream_video, monkeypatch):
    vid, src = stream_video
    monkeypatch.setattr(transcribe, "transcribe",
                        lambda wav, *a, **k: {"language": "es", "model": "fake", "device": "cpu",
                                              "segments": _segments(250)})
    monkeypatch.setattr(llm, "status", lambda url: {"available": True, "models": ["qwen3:14b", "gemma4:12b"]})
    used = []

    def fake_chat(url, model, system, user, schema, **kw):
        used.append(model)
        if "events" in schema["properties"]:
            a = json.loads(user)["tramo"].split("s")[0]
            t0 = float(a)
            return {"events": [{"t": t0 + 5, "type": "punch"}, {"t": t0 + 9, "type": "meme", "text": "¡NO PUEDE SER!"},
                               {"t": t0 + 14, "type": "shake"}, {"t": t0 + 20, "type": "cam"},
                               {"t": t0 + 26, "type": "callout", "text": "el jefe final"}]}
        if "beats" in schema["properties"]:
            data = json.loads(user)
            return {"beats": [{"id": b["id"], "score": 90 if b["id"] % 2 == 0 else 20,
                               "chapter": "Primer jefe" if b["id"] < 2 else "La remontada"} for b in data]}
        return {"titles": ["Lo mejor del stream"], "description": "Un resumen.", "tags": ["gaming"],
                "thumbnail_text": ["¡NO PUEDE SER!"]}
    monkeypatch.setattr(llm, "chat_json", fake_chat)

    assert client.post(f"/api/stream/videos/{vid}/youtube", json={"target_min": 15}).status_code == 400
    assert client.get("/api/ai/models").json()["models"] == ["qwen3:14b", "gemma4:12b"]
    assert client.post(f"/api/stream/videos/{vid}/youtube", json={"target_min": 3, "llm_model": "nope:1b"}).status_code == 400
    client.put(f"/api/stream/videos/{vid}", json={"facecam": {"x": 0.7, "y": 0.65, "w": 0.28, "h": 0.33}, "cam_mode": "always"})
    client.post("/api/feedback", json={"kind": "youtube", "rating": -1, "note": "muy lento, más memes"})
    j = client.post(f"/api/stream/videos/{vid}/youtube", json={"target_min": 3, "llm_model": "gemma4:12b"}).json()
    j = client.get(f"/api/jobs/{j['id']}").json()
    assert j["status"] == "done", j
    res = j["result"]
    assert set(used) == {"gemma4:12b"} and res["model"] == "gemma4:12b"
    from app import db
    assert db.get_setting("ollama_model") == "qwen3:14b"  # el modelo elegido no cambia Configuración
    assert 150 <= res["duration"] <= 260
    st = client.get(f"/api/stream/videos/{vid}").json()
    yt = st["youtube"][0]
    assert yt["model"] == "gemma4:12b" and yt["options"]["target_min"] == 3
    assert "Capítulos:" in yt["description"] and "00:00" in yt["description"]
    assert yt["thumbnails"]
    assert client.get(f"/api/stream/thumbs/{yt['thumbnails'][0]}").status_code == 200

    assert res["director"]["punch"] >= 1 and res["director"]["meme"] >= 1 and res["director"]["cam"] >= 1
    p = client.get(f"/api/studio/projects/{res['project_id']}").json()
    assert p["format"] == "horizontal"
    vtr = p["timeline"]["tracks"]["video"]
    assert any(x.get("rect") for x in vtr) and any(x.get("fx") for x in vtr)
    assert any(t["kind"] == "meme" for t in p["timeline"]["tracks"]["text"])
    assert any(t["kind"] == "lower" for t in p["timeline"]["tracks"]["text"])
    r = client.post(f"/api/studio/projects/{res['project_id']}/render", json={"quality": "preview"}).json()
    r = client.get(f"/api/jobs/{r['id']}").json()
    assert r["status"] == "done", r
    client.delete(f"/api/studio/projects/{res['project_id']}")


@needs_ffmpeg
def test_many_cuts_short_command(client, stream_video, tmp_path):
    """Cientos de cortes en una fuente larga: el comando de FFmpeg debe caber en el límite de Windows."""
    from app.studio import render as srender
    vid, src = stream_video
    segs = [{"id": f"v{i}", "src_in": i * 1.0, "src_out": i * 1.0 + 0.6, "start": i * 0.6, "zoom": 1.0, "crop_x": 0.5}
            for i in range(240)]
    p = {"source_path": str(src), "source_kind": "video",
         "source_meta": {"duration": 250, "width": 640, "height": 360, "has_video": True, "has_audio": True},
         "timeline": {"width": 1920, "height": 1080, "fps": 30, "duration": 144, "words": [], "captions": {"enabled": False},
                      "tracks": {"video": segs, "broll": [], "text": [], "music": [], "sfx": []}},
         "style": {}}
    from app.studio import style as style_mod
    p["style"] = style_mod.clean_style({})
    work = tmp_path / "w"
    work.mkdir()
    cmd, dur, enc = srender.build_command(p, "preview", work, tmp_path / "o.mp4")
    assert len(subprocess.list2cmdline(cmd)) < 30000


def test_jump_cuts_keep_action_cut_dead_air():
    from app.studio.director import jump_cut_ranges
    words = [{"start": 0.0, "end": 2.0, "word": "hola"}, {"start": 10.0, "end": 12.0, "word": "otra"},
             {"start": 20.0, "end": 22.0, "word": "fin"}]
    energy = [0.1] * 20 + [0.9] * 20 + [0.1] * 10  # 0-10 s quieto, 10-20 s acción, 20-25 s quieto (hop 0.5)
    keep = jump_cut_ranges(0, 25, words, energy, gap_thr=1.5, low_energy=0.4)
    assert keep[0] == (0, 2.25)            # el silencio 2-10 s (quieto) se corta
    assert any(a <= 13 and b >= 19 for a, b in keep)  # 12-20 s sin voz pero con acción: se queda
    assert sum(b - a for a, b in keep) < 25
