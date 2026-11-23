"""Miniaturas: composición, flujo completo con IA simulada, edición de texto y elección para publicar."""
from PIL import Image, ImageDraw

from app.ai import llm
from app.thumbs import compose, engine, store
from conftest import make_video, needs_ffmpeg


def _subject():
    im = Image.new("RGBA", (400, 600), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.ellipse([100, 40, 300, 260], fill=(230, 180, 150, 255))
    d.rectangle([60, 260, 340, 600], fill=(200, 120, 60, 255))
    return im


def test_compose_layouts(tmp_path):
    bg = Image.new("RGB", (1920, 1080), (30, 120, 200))
    logo = Image.new("RGBA", (300, 150), (255, 0, 0, 255))
    for lay in ("left", "right", "center"):
        c = {"text": "Eneba no entrega", "highlight_word": "Eneba", "text_color": "#FFFFFF", "accent_color": "#FFD400",
             "layout": lay, "background": "frame", "elements": [{"asset": "w0", "position": "top_right", "size": "medium"}],
             "arrow": True, "highlight_box": lay == "right"}
        out = compose.compose(c, _subject(), bg, {"w0": logo}, tmp_path / f"{lay}.jpg")
        im = Image.open(out)
        assert im.size == (1280, 720)
    g = compose.compose({"text": "Hola", "background": "gradient", "layout": "left"}, None, None, {}, tmp_path / "g.jpg")
    assert Image.open(g).size == (1280, 720)
    assert compose.split_lines("NO TENGO MI CODIGO") == ["NO TENGO", "MI CODIGO"]


@needs_ffmpeg
def test_generate_flow(client, dirs, monkeypatch):
    v = make_video(dirs["videos"] / "mini.mp4", 640, 360, 4)
    r = client.post("/api/library/import", json={"paths": [str(v)]}).json()
    x = r["results"][0]
    vid = x["video"]["id"] if x.get("video") else x["video_id"]
    # sin rostros en el video de prueba: forzamos un candidato
    monkeypatch.setattr(engine, "candidate_frames", lambda src, work, **k: [
        {"t": 1.0, "face": [0.4, 0.2, 0.2, 0.3], "score": 1.0, "sample": str(engine.grab(src["path"], 1.0, work / "s0.jpg", 320))}])
    calls = {"design": 0}

    def fake_chat(url, model, system, user, schema, **kw):
        props = schema["properties"]
        if "frames" in props:
            return {"frames": [{"i": 0, "expresion": "sorpresa", "intensidad": 8, "apta": 9}]}
        if "concepts" in props:
            calls["design"] += 1
            assert "INSTRUCCIONES DEL CREADOR" in user and "serio" in user
            return {"concepts": [{"text": "SIN CÓDIGO", "highlight_word": "CÓDIGO", "text_color": "#FFFFFF",
                                  "accent_color": "#FF2D2D", "layout": "left", "face": 0, "background": "frame",
                                  "elements": [], "idea": "x"},
                                 {"text": "ENEBA FALLÓ", "highlight_word": "FALLÓ", "text_color": "#FFFFFF",
                                  "accent_color": "#FFD400", "layout": "right", "face": 0, "background": "gradient",
                                  "elements": [], "idea": "y"}]}
        if "scores" in props:
            return {"scores": [{"i": 0, "legibilidad": 9, "curiosidad": 8, "contraste": 8, "emocion": 7, "fiel_al_tema": 9,
                                "critica": "bien"}, {"i": 1, "legibilidad": 7, "curiosidad": 6, "contraste": 7, "emocion": 5,
                                                     "fiel_al_tema": 8, "critica": "más cara"}]}
        if "consultas" in props:
            return {"tema": "Eneba no entrega claves de GTA 6", "consultas": ["eneba gta 6"]}
        return {}
    monkeypatch.setattr(llm, "chat_json", fake_chat)
    job = client.post("/api/thumbs/generate", json={"source_kind": "video", "source_id": vid, "n": 2, "use_web": False,
                                                    "instructions": "video serio, sin memes"}).json()
    j = client.get(f"/api/jobs/{job['id']}").json()
    assert j["status"] == "done", j
    assert len(j["result"]["ids"]) == 2 and calls["design"] == 1  # nota >= 6.5: sin ronda de corrección
    thumbs = client.get("/api/thumbs", params={"kind": "video", "id": vid}).json()
    assert {t["concept"]["text"] for t in thumbs} == {"SIN CÓDIGO", "ENEBA FALLÓ"}
    best = max(thumbs, key=lambda t: t["score"])
    assert best["concept"]["text"] == "SIN CÓDIGO" and best["score"] > 8
    assert client.get(best["url"]).status_code == 200
    ed = client.patch(f"/api/thumbs/{best['id']}", json={"text": "ME QUEDÉ SIN GTA", "highlight_word": "GTA"}).json()
    assert ed["concept"]["text"] == "ME QUEDÉ SIN GTA"
    client.post(f"/api/thumbs/{best['id']}/choose")
    assert store.chosen_for("video", vid)["id"] == best["id"]
    up = client.post("/api/thumbs/upload", files={"file": ("ej.png", open(best["path"], "rb"), "image/png")}).json()
    assert up["ref"].startswith("upload:") and client.get(up["url"]).status_code == 200


def test_thumbs_linked_project_and_render(client, tmp_path):
    """Una miniatura hecha para el proyecto aparece al publicar su render, y elegirla vale para ambos."""
    from app import db
    with db.get_conn() as conn:
        pid = conn.execute("INSERT INTO studio_projects (name, format, source_path, source_kind, source_meta, transcript, "
                           "timeline, style) VALUES ('p', 'horizontal', 'x.mp4', 'video', '{}', '{}', '{}', '{}')").lastrowid
        rid = conn.execute("INSERT INTO studio_renders (project_id, path, quality) VALUES (?, 'r.mp4', 'final')", (pid,)).lastrowid
    img = tmp_path / "t.jpg"
    Image.new("RGB", (1280, 720), "red").save(img)
    a = store.save("b1", "project", pid, {}, img, 7.0, "", {}, {})
    b = store.save("b2", "studio_render", rid, {}, img, 8.0, "", {}, {})
    got = {t["id"] for t in client.get(f"/api/thumbs?kind=studio_render&id={rid}").json()}
    assert {a, b} <= got
    store.choose(a)
    store.choose(b)
    flags = {t["id"]: t["chosen"] for t in store.list_for("project", pid)}
    assert flags[b] and not flags[a]  # una sola elegida para el mismo video
    assert store.chosen_for("studio_render", rid)["id"] == b


def test_publish_source_studio_render_resolves(client, tmp_path):
    """Publicar un video del Studio no debe tronar (antes: NameError por `p["id"]`)."""
    from app import db
    from app.publish import sources
    vid = make_video(tmp_path / "final.mp4")
    with db.get_conn() as conn:
        pid = conn.execute("INSERT INTO studio_projects (name, format, source_path, source_kind, source_meta, transcript, "
                           "timeline, style) VALUES ('Mi video', 'horizontal', 'x.mp4', 'video', '{}', '{}', '{}', '{}')").lastrowid
        rid = conn.execute("INSERT INTO studio_renders (project_id, path, quality) VALUES (?, ?, 'final')", (pid, str(vid))).lastrowid
    info = sources.resolve("studio_render", rid)
    assert info["project_id"] == pid or info.get("info", {}).get("project_id") == pid


def test_studio_finals_listed_and_sent_to_clips(client, tmp_path):
    """Los videos editados en el Studio salen en su propia lista y se pueden llevar a «Clips con IA» (una sola vez)."""
    from app import db
    vid = make_video(tmp_path / "final2.mp4")
    with db.get_conn() as conn:
        pid = conn.execute("INSERT INTO studio_projects (name, format, source_path, source_kind, source_meta, transcript, "
                           "timeline, style) VALUES ('Eneba', 'horizontal', 'x.mp4', 'video', '{}', '{}', '{}', '{}')").lastrowid
        rid = conn.execute("INSERT INTO studio_renders (project_id, path, quality) VALUES (?, ?, 'final')", (pid, str(vid))).lastrowid
    fin = {f["id"]: f for f in client.get("/api/studio/finals").json()}
    assert fin[rid]["available"] and fin[rid]["library_video_id"] is None
    a = client.post(f"/api/studio/renders/{rid}/to-library").json()
    b = client.post(f"/api/studio/renders/{rid}/to-library").json()
    assert a["created"] and not b["created"] and a["video_id"] == b["video_id"]
    ov = {v["id"]: v for v in client.get("/api/videos/overview").json()}
    assert ov[a["video_id"]]["from_studio"] and ov[a["video_id"]]["title"] == "Studio: Eneba"
    assert {f["id"]: f for f in client.get("/api/studio/finals").json()}[rid]["library_video_id"] == a["video_id"]
    client.delete(f"/api/library/{a['video_id']}")
