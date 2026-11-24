"""Pruebas del Studio (contenido original). Whisper y Ollama se simulan; FFmpeg es real."""
import json
import subprocess
from pathlib import Path

import pytest

from conftest import needs_ffmpeg
from app.ai import llm, transcribe
from app.studio import agent, autoedit
from app.studio import timeline as tlm

# Habla simulada: frases con pausas largas y una muletilla
WORDS = (
    [("Hoy", 0.5, 0.8), ("te", 0.85, 1.0), ("cuento", 1.05, 1.5), ("mi", 1.55, 1.7), ("secreto.", 1.75, 2.6)]
    + [("eh", 4.6, 4.9)]
    + [("Llevo", 6.0, 6.4), ("cinco", 6.45, 6.9), ("años", 6.95, 7.4), ("creando", 7.45, 8.1), ("videos.", 8.15, 9.0)]
    + [("Y", 12.0, 12.2), ("funciona", 12.25, 13.0), ("increíble!", 13.05, 14.2), ("Pruébalo", 14.3, 15.0),
       ("hoy.", 15.05, 15.5)]
)


def fake_tr():
    segs, cur = [], []
    for w, a, b in WORDS:
        cur.append({"start": a, "end": b, "word": w})
        if w.endswith((".", "!")) or w == "eh":
            segs.append({"start": cur[0]["start"], "end": cur[-1]["end"],
                         "text": " ".join(x["word"] for x in cur), "words": cur})
            cur = []
    return {"language": "es", "model": "fake", "device": "cpu", "segments": segs}


def speech_audio_expr():
    # tono solo donde hay palabras → silencios reales para que la energía sea coherente
    conds = "+".join(f"between(t,{a},{b})" for _, a, b in WORDS)
    return f"aevalsrc='0.5*sin(2*PI*220*t)*gt({conds},0)':d=20:s=48000"


@pytest.fixture()
def fakes(monkeypatch):
    monkeypatch.setattr(transcribe, "transcribe", lambda *a, **k: fake_tr())
    monkeypatch.setattr(llm, "status", lambda url: {"available": False, "models": []})


@pytest.fixture(scope="module")
def media_files(dirs):
    d = dirs["videos"] / "studio"
    d.mkdir(exist_ok=True)
    video = d / "mi_toma.mp4"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=30:duration=20",
                    "-f", "lavfi", "-i", speech_audio_expr(), "-c:v", "libx264", "-preset", "ultrafast",
                    "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(video)], check=True)
    voice = d / "voz_en_off.wav"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", speech_audio_expr(), str(voice)], check=True)
    music_dir = d / "musica"
    music_dir.mkdir(exist_ok=True)
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
                    "aevalsrc='0.3*sin(2*PI*110*t)*(0.6+0.4*sin(2*PI*2*t))':d=30:s=48000",
                    str(music_dir / "beat lofi tranquilo.wav")], check=True)
    broll_dir = d / "broll"
    broll_dir.mkdir(exist_ok=True)
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "mandelbrot=size=640x360:rate=25",
                    "-t", "6", "-pix_fmt", "yuv420p", str(broll_dir / "ciudad noche.mp4")], check=True)
    return {"video": video, "voice": voice, "music_dir": music_dir, "broll_dir": broll_dir}


def _upload(client, path: Path):
    with open(path, "rb") as f:
        r = client.post("/api/studio/upload", files={"file": (path.name, f, "application/octet-stream")})
    assert r.status_code == 200, r.text
    return r.json()["upload_id"]


def _probe(path):
    out = json.loads(subprocess.run(["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json",
                                     str(path)], capture_output=True, text=True).stdout)
    v = next((s for s in out["streams"] if s["codec_type"] == "video"), None)
    a = next((s for s in out["streams"] if s["codec_type"] == "audio"), None)
    return v, a, float(out["format"]["duration"])


# ---------- unidades ----------

def test_segments_cut_silences_and_fillers():
    words = [{"word": w, "start": a, "end": b} for w, a, b in WORDS]
    kept = autoedit.keep_words(words)
    assert "eh" not in [w["word"] for w in kept]
    segs = autoedit.build_segments(kept, 20.0, silence=0.45)
    assert len(segs) == 3
    total = sum(b - a for a, b in segs)
    assert 9 < total < 11  # de 20 s de toma quedan ~10 s de voz


def test_remove_range_ripples_overlays():
    tl = tlm.empty()
    tl["tracks"]["video"] = [{"id": "v1", "src_in": 0, "src_out": 10, "start": 0, "zoom": 1, "crop_x": .5}]
    tl["duration"] = 10
    tl["tracks"]["text"] = [{"id": "t1", "start": 6, "end": 8, "text": "hola", "kind": "callout"},
                            {"id": "t2", "start": 2.2, "end": 2.8, "text": "borrar", "kind": "callout"}]
    tl["tracks"]["sfx"] = [{"id": "s1", "start": 7, "asset_id": 1, "volume": .5}]
    removed = tlm.remove_range(tl, 2, 4)
    assert removed == 2
    assert [s["src_in"] for s in tl["tracks"]["video"]] == [0, 4]
    texts = {t["id"]: t for t in tl["tracks"]["text"]}
    assert "t2" not in texts and texts["t1"]["start"] == 4
    assert tl["tracks"]["sfx"][0]["start"] == 5


def test_split():
    tl = tlm.empty()
    tl["tracks"]["video"] = [{"id": "v1", "src_in": 0, "src_out": 10, "start": 0, "zoom": 1, "crop_x": .5}]
    assert tlm.split_at(tl, 4)
    assert [(s["src_in"], s["src_out"]) for s in tl["tracks"]["video"]] == [(0, 4), (4, 10)]


# ---------- flujo completo ----------

@needs_ffmpeg
def test_video_project_full_flow(client, fakes, media_files, monkeypatch):
    st = client.get("/api/studio/status").json()
    assert st["assets"]["sfx"] >= 7  # pack de efectos propio generado

    r = client.post("/api/studio/assets/scan", json={"folder": str(media_files["music_dir"])}).json()
    assert r["added"] == 1
    client.post("/api/studio/assets/scan", json={"folder": str(media_files["broll_dir"])})
    music = client.get("/api/studio/assets", params={"kind": "music"}).json()
    broll = client.get("/api/studio/assets", params={"kind": "broll"}).json()
    assert music[0]["title"] == "beat lofi tranquilo" and "lofi" in music[0]["tags"]
    broll = [b for b in broll if b["path"].startswith(str(media_files["broll_dir"]))]  # otras pruebas también añaden b-roll
    assert broll and broll[0]["media_type"] == "video"
    # Fuera de carpetas autorizadas → rechazado
    assert client.post("/api/studio/assets/scan", json={"folder": "/etc"}).status_code == 403

    up = _upload(client, media_files["video"])
    res = client.post("/api/studio/projects", json={"name": "Mi primer video", "source_type": "upload",
                                                    "upload_id": up}).json()
    pid = res["project"]["id"]
    job = client.get(f"/api/jobs/{res['job']['id']}").json()
    assert job["status"] == "done", job
    assert job["result"]["removed_seconds"] > 8

    p = client.get(f"/api/studio/projects/{pid}").json()
    tl = p["timeline"]
    assert len(tl["tracks"]["video"]) == 3
    assert 9 < tl["duration"] < 11.5
    assert [s["zoom"] > 1 for s in tl["tracks"]["video"]][:2] == [False, True]  # zoom alternado
    assert "eh" not in [w["word"] for w in tl["words"]]
    assert tl["tracks"]["text"][0]["kind"] == "title"  # título básico sin IA
    assert tl["tracks"]["music"] == []  # sin IA no se pone "la primera música" al azar
    assert p["can_undo"]

    # ---- instrucción a la IA (simulada) ----
    monkeypatch.setattr(llm, "status", lambda url: {"available": True, "models": ["qwen3:14b"]})
    pop = next(a for a in client.get("/api/studio/assets", params={"kind": "sfx"}).json() if "pop" in a["tags"])
    title_id = tl["tracks"]["text"][0]["id"]

    def fake_chat(url, model, system, user, schema, **kw):
        assert "INSTRUCCIÓN DEL USUARIO: haz el título amarillo" in user
        assert title_id in user  # la IA ve los ids del timeline
        return {"message": "Listo", "ops": [
            {"op": "set_style", "key": "title_bg", "value": "#FFE500"},
            {"op": "update_text", "id": title_id, "text": "Mi secreto"},
            {"op": "add_text", "start": 5, "end": 7, "text": "5 AÑOS", "kind": "callout"},
            {"op": "add_sfx", "asset_id": pop["id"], "time": 5},
            {"op": "add_broll", "asset_id": broll[0]["id"], "start": 1, "end": 3},
            {"op": "add_sfx", "asset_id": 99999, "time": 1},          # id inventado → se ignora
            {"op": "set_style", "key": "silence_threshold", "value": 9},  # clave no permitida → se ignora
        ]}

    monkeypatch.setattr(llm, "chat_json", fake_chat)
    j = client.post(f"/api/studio/projects/{pid}/instruct", json={"text": "haz el título amarillo"}).json()
    j = client.get(f"/api/jobs/{j['id']}").json()
    assert j["status"] == "done", j
    assert len(j["result"]["skipped"]) == 2
    p2 = client.get(f"/api/studio/projects/{pid}").json()
    assert p2["style"]["title_bg"] == "#FFE500"
    texts = {t["kind"]: t["text"] for t in p2["timeline"]["tracks"]["text"]}
    assert texts["title"] == "Mi secreto" and texts["callout"] == "5 AÑOS"
    assert len(p2["timeline"]["tracks"]["broll"]) == 1

    # ---- deshacer / rehacer ----
    u = client.post(f"/api/studio/projects/{pid}/undo").json()
    assert u["style"]["title_bg"] != "#FFE500" and u["can_redo"]
    r2 = client.post(f"/api/studio/projects/{pid}/redo").json()
    assert r2["style"]["title_bg"] == "#FFE500"

    # ---- edición manual: mover texto y borrar un segmento ----
    tl = r2["timeline"]
    tl["tracks"]["text"][-1]["start"] = 6.0
    tl["tracks"]["text"][-1]["end"] = 8.0
    del tl["tracks"]["video"][1]
    m = client.put(f"/api/studio/projects/{pid}/timeline", json={"timeline": tl}).json()
    assert len(m["timeline"]["tracks"]["video"]) == 2
    assert m["timeline"]["duration"] < r2["timeline"]["duration"]

    # ---- render vista previa y final ----
    j = client.post(f"/api/studio/projects/{pid}/render", json={"quality": "preview"}).json()
    j = client.get(f"/api/jobs/{j['id']}").json()
    assert j["status"] == "done", j
    renders = client.get(f"/api/studio/projects/{pid}/renders").json()
    v, a, d = _probe(renders[0]["path"])
    assert (v["width"], v["height"]) == (720, 1280) and a is not None
    assert abs(d - m["timeline"]["duration"]) < 0.4

    j = client.post(f"/api/studio/projects/{pid}/render", json={"quality": "final"}).json()
    j = client.get(f"/api/jobs/{j['id']}").json()
    assert j["status"] == "done", j
    final = [r for r in client.get(f"/api/studio/projects/{pid}/renders").json() if r["quality"] == "final"][0]
    v, a, d = _probe(final["path"])
    assert (v["width"], v["height"]) == (1080, 1920)
    part = client.get(final["url"], headers={"Range": "bytes=0-99"})
    assert part.status_code == 206

    # Al borrar el proyecto se borran sus renders y la subida, nunca tus archivos originales
    path = final["path"]
    client.delete(f"/api/studio/projects/{pid}")
    assert not Path(path).exists()
    assert media_files["video"].exists()


@needs_ffmpeg
def test_voice_only_project_renders_with_background(client, fakes, media_files):
    up = _upload(client, media_files["voice"])
    res = client.post("/api/studio/projects", json={"name": "Voz en off", "source_type": "upload",
                                                    "upload_id": up, "format": "vertical"}).json()
    pid = res["project"]["id"]
    assert res["project"]["source_kind"] == "audio"
    assert client.get(f"/api/jobs/{res['job']['id']}").json()["status"] == "done"
    p = client.get(f"/api/studio/projects/{pid}").json()
    assert all(s["zoom"] == 1.0 for s in p["timeline"]["tracks"]["video"])  # sin zoom en solo audio
    j = client.post(f"/api/studio/projects/{pid}/render", json={"quality": "preview"}).json()
    j = client.get(f"/api/jobs/{j['id']}").json()
    assert j["status"] == "done", j
    v, a, d = _probe(client.get(f"/api/studio/projects/{pid}/renders").json()[0]["path"])
    assert v is not None and (v["width"], v["height"]) == (720, 1280)
    client.delete(f"/api/studio/projects/{pid}")


@needs_ffmpeg
def test_enrich_with_ai_and_reference_style(client, fakes, media_files, monkeypatch):
    monkeypatch.setattr(llm, "status", lambda url: {"available": True, "models": ["qwen3:14b"]})
    sfx = client.get("/api/studio/assets", params={"kind": "sfx"}).json()
    impact = next(a for a in sfx if "impacto" in a["tags"])

    def fake_chat(url, model, system, user, schema, **kw):
        if "events" in schema["properties"]:  # director
            return {"events": [{"t": 6.0, "type": "meme", "text": "¡CINCO AÑOS!"}, {"t": 3.5, "type": "punch"}]}
        if "tema" in schema["properties"]:  # paso 1: entender el video
            return {"tema": "el secreto del creador", "postura": "funciona", "hechos": ["cinco años"], "no_confundir": []}
        if "ok" in schema["properties"]:  # paso 3: verificar el título
            return {"ok": True, "titulo": ""}
        assert "CATÁLOGO" in user and "TRANSCRIPCIÓN" in user
        return {"title": "El secreto que nadie cuenta",
                "callouts": [{"start": 5.5, "end": 7, "text": "5 AÑOS"}, {"start": 0.5, "end": 1, "text": "tapa título"}],
                "broll": [{"asset_id": 424242, "start": 1, "end": 3}],
                "emphasis": [{"time": 8.0, "sfx_asset_id": impact["id"]}]}

    monkeypatch.setattr(llm, "chat_json", fake_chat)
    up = _upload(client, media_files["video"])
    res = client.post("/api/studio/projects", json={"name": "Con IA", "source_type": "upload", "upload_id": up}).json()
    job = client.get(f"/api/jobs/{res['job']['id']}").json()
    assert job["status"] == "done" and job["result"]["ai"]["used"]
    p = client.get(f"/api/studio/projects/{res['project']['id']}").json()
    texts = p["timeline"]["tracks"]["text"]
    assert texts[0]["text"] == "El secreto que nadie cuenta"
    assert [t["text"] for t in texts if t["kind"] == "callout"] == ["5 AÑOS"]  # el que tapaba el título se omite
    assert job["result"]["ai"]["director"]["punch"] >= 1
    assert p["timeline"]["tracks"]["broll"] == []  # asset inventado → ignorado
    snd = job["result"]["ai"].get("sound") or {}
    kept = any(s["asset_id"] == impact["id"] for s in p["timeline"]["tracks"]["sfx"])
    # el efecto de énfasis se pone; el juez de sonido puede quitarlo solo por presupuesto/espacio (clip de 10 s)
    assert kept or any(("sobran" in c["motivo"] or "pegado" in c["motivo"]) for c in snd.get("changes", [])), snd

    # Estilo desde un video de referencia
    ref = _upload(client, media_files["video"])
    j = client.post(f"/api/studio/projects/{res['project']['id']}/reference", json={"upload_id": ref}).json()
    j = client.get(f"/api/jobs/{j['id']}").json()
    assert j["status"] == "done", j
    assert "cuts_per_min" in j["result"]
    client.delete(f"/api/studio/projects/{res['project']['id']}")


def test_style_validation(client):
    from app.studio.style import clean_style
    s = clean_style({"caption_color": "rojo", "zoom_amount": 9, "caption_position": "izquierda", "remove_fillers": 0})
    assert s["caption_color"] == "#FFFFFF" and s["zoom_amount"] == 1.5
    assert s["caption_position"] == "auto" and s["remove_fillers"] is False


@needs_ffmpeg
def test_render_with_color_emojis(client, fakes, media_files):
    from app.studio import emoji, projects as pj, render
    if not emoji.font_path():
        pytest.skip("sin fuente de emojis a color")
    up = _upload(client, media_files["video"])
    res = client.post("/api/studio/projects", json={"name": "Emojis", "source_type": "upload", "upload_id": up,
                                                    "use_ai": False}).json()
    pid = res["project"]["id"]
    st = client.get(f"/api/studio/projects/{pid}").json()["style"]
    client.put(f"/api/studio/projects/{pid}/style", json={"style": {**st, "caption_emojis": True, "caption_style": "karaoke"}})
    p = pj.get(pid)
    p["timeline"]["captions"]["enabled"] = True
    work = Path(p["source_path"]).parent / "emoji_test"
    work.mkdir(exist_ok=True)
    cmd, _, _ = render.build_command(p, "preview", work, work / "o.mp4")
    assert any(str(c).startswith("emoji") and str(c).endswith(".png") for c in cmd)
    assert (work / "emoji0.png").exists()
    pj.save(pid, p["timeline"])
    j = client.post(f"/api/studio/projects/{pid}/render", json={"quality": "preview"}).json()
    assert client.get(f"/api/jobs/{j['id']}").json()["status"] == "done"
    import os, shutil
    if os.environ.get("KEEP_EMOJI_RENDER"):
        shutil.copy(client.get(f"/api/studio/projects/{pid}/renders").json()[0]["path"], os.environ["KEEP_EMOJI_RENDER"])
    client.delete(f"/api/studio/projects/{pid}")
