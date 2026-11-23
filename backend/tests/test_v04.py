"""Pruebas v0.4: transiciones, b-roll de tus videos, índice de clips, video desde un tema y estilos aprendidos.
La IA (visión, guion), Whisper y la voz se simulan; FFmpeg es real."""
import json
import subprocess
from pathlib import Path

import pytest

from conftest import make_video, needs_ffmpeg
from app.ai import llm, transcribe
from app.editor.crop import track_expr
from app.studio import voice


def _probe(path):
    out = json.loads(subprocess.run(["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json",
                                     str(path)], capture_output=True, text=True).stdout)
    v = next((s for s in out["streams"] if s["codec_type"] == "video"), None)
    return v, float(out["format"]["duration"])


def test_track_expr_interpolates():
    e = track_expr([(10.0, 0.2), (11.0, 0.8)], crop_w=600, src_w=1920, t0=10.0)
    assert e.startswith("if(lt(t,0.00),84,")
    assert "if(lt(t,1.00),84+(1152)*(t-0.00)/1.00,1236)" in e


@pytest.fixture()
def gameplay(client, dirs):
    v1 = make_video(dirs["videos"] / "gameplay" / "partida1.mp4", w=1280, h=720, seconds=30)
    v2 = make_video(dirs["videos"] / "gameplay" / "partida2.mp4", w=1280, h=720, seconds=30)
    r = client.post("/api/studio/footage/import-all").json()
    assert r["imported"] >= 2
    lib = client.get("/api/library").json()["items"]
    ids = [x["id"] for x in lib if x["title"] in ("partida1", "partida2")]
    yield ids
    for i in ids:
        client.delete(f"/api/library/{i}")


@needs_ffmpeg
def test_footage_index_topic_and_render(client, gameplay, monkeypatch):
    monkeypatch.setattr(llm, "status", lambda url: {"available": True, "models": ["qwen3:14b", "qwen3.6:35b-a3b"]})
    monkeypatch.setattr(llm, "unload", lambda url, model: None)
    calls = {"vision": 0}

    def fake_chat(url, model, system, user, schema, **kw):
        if kw.get("images"):
            calls["vision"] += 1
            return {"caption": f"Jugador construyendo una casa {calls['vision']}", "tags": ["minecraft", "construccion"],
                    "action": 60, "talking_head": False, "useful": True}
        assert "CATÁLOGO DE TOMAS" in user and "construyendo" in user
        shots = [int(x) for x in __import__("re").findall(r'"id": (\d+)', user)][:4]
        return {"title": "Construye mejor en Minecraft", "music_mood": "upbeat", "lines": [
            {"text": "¿Sabías que el 90 por ciento construye mal?", "emotion": "sorpresa", "on_screen": "90%",
             "shot_ids": shots[:2], "sfx": "impacto"},
            {"text": "Esto me desespera, siempre el mismo error.", "emotion": "enojo", "on_screen": "",
             "shot_ids": [shots[2], 999999], "sfx": "whoosh"},
            {"text": "Sígueme para más trucos.", "emotion": "entusiasmo", "on_screen": "SÍGUEME",
             "shot_ids": [], "sfx": ""}]}

    monkeypatch.setattr(llm, "chat_json", fake_chat)

    # 1) Índice de clips con IA de visión
    j = client.post("/api/studio/footage/index", json={"video_ids": gameplay}).json()
    j = client.get(f"/api/jobs/{j['id']}").json()
    assert j["status"] == "done", j
    assert j["result"]["videos"] == 2 and j["result"]["shots"] >= 6
    shots = client.get(f"/api/studio/footage/{gameplay[0]}/shots").json()
    assert shots and "construyendo" in shots[0]["caption"]
    assert client.get(shots[0]["thumb_url"]).status_code == 200

    # 2) Video desde un tema (voz simulada con FFmpeg)
    def fake_synth(lines, work, progress=lambda p, s: None, use_profile=True):
        work.mkdir(parents=True, exist_ok=True)
        out = work / "narracion.wav"
        dur = 3.0 * len(lines)
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
                        f"aevalsrc='0.4*sin(2*PI*200*t)':d={dur}:s=48000", "-ac", "2", str(out)], check=True)
        return out, [{"start": i * 3.0, "end": i * 3.0 + 2.7, "text": l["text"], "emotion": l["emotion"]}
                     for i, l in enumerate(lines)]

    monkeypatch.setattr(voice, "synthesize", fake_synth)
    monkeypatch.setattr(voice, "tts_available", lambda: True)
    monkeypatch.setattr(transcribe, "transcribe", lambda *a, **k: {"language": "es", "model": "f", "device": "cpu",
        "segments": [{"start": 0.2, "end": 2.5, "text": "Sabías que", "words": [
            {"start": 0.2, "end": 0.6, "word": "¿Sabías"}, {"start": 0.65, "end": 1.0, "word": "que"}]}]})
    j = client.post("/api/studio/topic", json={"topic": "errores al construir en Minecraft", "duration": 30}).json()
    j = client.get(f"/api/jobs/{j['id']}").json()
    assert j["status"] == "done", j
    pid = j["result"]["project_id"]
    p = client.get(f"/api/studio/projects/{pid}").json()
    tl = p["timeline"]
    assert p["source_kind"] == "audio" and abs(tl["duration"] - 9.0) < 0.2
    br = tl["tracks"]["broll"]
    assert len(br) >= 4 and all(b.get("video_id") for b in br)  # tomas de tus videos (id inventado descartado)
    assert br[0]["transition"] == "cut" and any(b["transition"] != "cut" for b in br[1:])
    texts = [t["text"] for t in tl["tracks"]["text"]]
    assert texts[0] == "Construye mejor en Minecraft" and "SÍGUEME" in texts
    assert len(tl["tracks"]["sfx"]) == 2

    # 3) Ajustes de b-roll: fondo desenfocado + audio original + todas las transiciones
    for b, tr in zip(br, ["slide", "zoom", "flash", "fade", "glitch"]):
        b["transition"] = tr
    br[0]["fit"] = "blur"
    br[1]["volume"] = 0.3
    m = client.put(f"/api/studio/projects/{pid}/timeline", json={"timeline": tl}).json()
    assert m["timeline"]["tracks"]["broll"][0]["fit"] == "blur"
    r = client.post(f"/api/studio/projects/{pid}/render", json={"quality": "preview"}).json()
    r = client.get(f"/api/jobs/{r['id']}").json()
    assert r["status"] == "done", r
    v, d = _probe(client.get(f"/api/studio/projects/{pid}/renders").json()[0]["path"])
    assert (v["width"], v["height"]) == (720, 1280) and abs(d - 9.0) < 0.4

    # 4) La IA puede cambiar transiciones por instrucción
    def instr(url, model, system, user, schema, **kw):
        return {"message": "ok", "ops": [{"op": "set_transition", "transition": "glitch"}]}
    monkeypatch.setattr(llm, "chat_json", instr)
    j = client.post(f"/api/studio/projects/{pid}/instruct", json={"text": "pon glitch en todas"}).json()
    assert client.get(f"/api/jobs/{j['id']}").json()["status"] == "done"
    p2 = client.get(f"/api/studio/projects/{pid}").json()
    assert all(b["transition"] == "glitch" for b in p2["timeline"]["tracks"]["broll"])
    client.delete(f"/api/studio/projects/{pid}")


@needs_ffmpeg
def test_video_segment_transitions_render(client, dirs, monkeypatch):
    """La toma principal con transiciones zoom/destello/fundido/glitch/barrido y seguimiento de rostro."""
    monkeypatch.setattr(llm, "status", lambda url: {"available": False, "models": []})
    src = make_video(dirs["videos"] / "studio_tr" / "toma.mp4", w=1280, h=720, seconds=12)
    with open(src, "rb") as f:
        up = client.post("/api/studio/upload", files={"file": ("toma.mp4", f, "video/mp4")}).json()["upload_id"]
    res = client.post("/api/studio/projects", json={"name": "Transiciones", "source_type": "upload",
                                                    "upload_id": up, "auto_edit": False}).json()
    pid = res["project"]["id"]
    tl = res["project"]["timeline"]
    seg = tl["tracks"]["video"][0]
    cuts = [0, 2, 4, 6, 8, 10, 12]
    tl["tracks"]["video"] = [dict(seg, id=f"v{i}", src_in=a, src_out=b,
                                  transition=["cut", "zoom", "flash", "fade", "glitch", "whip"][i],
                                  zoom=1.12 if i % 2 else 1.0)
                             for i, (a, b) in enumerate(zip(cuts, cuts[1:]))]
    tl["tracks"]["video"][1]["track"] = [[2.0, 0.2], [3.0, 0.8], [4.0, 0.5]]
    client.put(f"/api/studio/projects/{pid}/timeline", json={"timeline": tl})
    r = client.post(f"/api/studio/projects/{pid}/render", json={"quality": "preview"}).json()
    r = client.get(f"/api/jobs/{r['id']}").json()
    assert r["status"] == "done", r
    v, d = _probe(client.get(f"/api/studio/projects/{pid}/renders").json()[0]["path"])
    assert abs(d - 12.0) < 0.4  # las transiciones no cambian la duración
    client.delete(f"/api/studio/projects/{pid}")


@needs_ffmpeg
def test_learn_style_from_reference(client, dirs, monkeypatch):
    ref = dirs["videos"] / "ref" / "viral.mp4"
    ref.parent.mkdir(parents=True, exist_ok=True)
    # Video con muchos cortes (colores distintos cada 1 s) y destellos blancos
    parts = []
    for i, c in enumerate(["red", "blue", "white", "green", "yellow", "white", "purple", "orange"]):
        parts.append(f"color=c={c}:s=360x640:d=1:r=30[c{i}]")
    graph = ";".join(parts) + ";" + "".join(f"[c{i}]" for i in range(8)) + "concat=n=8:v=1:a=0[v]"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-filter_complex", graph, "-f", "lavfi", "-i",
                    "aevalsrc='0.4*sin(2*PI*300*t)':d=8:s=48000", "-map", "[v]", "-map", "0:a", "-shortest",
                    "-pix_fmt", "yuv420p", str(ref)], check=True)
    client.post("/api/library/import", json={"paths": [str(ref)]})
    vid = next(x["id"] for x in client.get("/api/library").json()["items"] if x["title"] == "viral")

    monkeypatch.setattr(llm, "status", lambda url: {"available": True, "models": ["qwen3:14b", "qwen3.6:35b-a3b"]})
    monkeypatch.setattr(llm, "unload", lambda url, model: None)
    monkeypatch.setattr(transcribe, "transcribe", lambda *a, **k: {"language": "es", "segments": [
        {"start": 0.1, "end": 7.5, "text": "x", "words": [{"start": 0.1 + i * 0.25, "end": 0.3 + i * 0.25, "word": "hola"}
                                                         for i in range(28)]}]})

    def fake_chat(url, model, system, user, schema, **kw):
        if kw.get("images"):
            return {"captions": True, "caption_position": "middle", "words_on_screen": 2, "uppercase": True,
                    "text_color": "#FFFFFF", "highlight_color": "#00FF88", "font_style": "condensed",
                    "title_box": True, "title_bg": "#FF0055", "layout": "talking_head", "zoomed_in": True,
                    "emojis": True, "graphics": "flechas"}
        return {"markdown": "## El formato en una línea\nCortes cada segundo.\n## Qué copiar\n- Destellos"}

    monkeypatch.setattr(llm, "chat_json", fake_chat)
    j = client.post("/api/studio/learn", json={"video_id": vid, "name": "Estilo rápido"}).json()
    j = client.get(f"/api/jobs/{j['id']}").json()
    assert j["status"] == "done", j
    st = client.get("/api/studio/styles").json()[0]
    assert st["name"] == "Estilo rápido"
    assert st["profile"]["edit"]["cuts_per_10s"] >= 5
    assert st["profile"]["delivery"]["words_per_min"] > 150
    s = st["style"]
    assert s["caption_position"] == "middle" and s["caption_max_words"] == 2
    assert s["highlight_color"] == "#00FF88" and s["caption_font"] == "Impact" and s["title_bg"] == "#FF0055"
    assert s["zoom_every"] == 1 and "Destellos" in st["report"]

    # Aplicar el estilo aprendido al render de un clip
    clip = client.post("/api/clips", json={"video_id": vid, "start_s": 0.5, "end_s": 6.0, "title": "Prueba"}).json()
    r = client.post(f"/api/clips/{clip['id']}/render", json={"crop_mode": "center", "style_id": st["id"],
                                                             "show_title": True}).json()
    assert client.get(f"/api/jobs/{r['id']}").json()["status"] == "done"
    client.delete(f"/api/library/{vid}")
