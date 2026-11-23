"""Brief del proyecto: tipo de video, estilos, subtítulos limpios, adjuntos y agrupación de subtítulos."""
from app import db
from app.editor.subtitles import group_words
from app.studio import agent, brief, render
from test_studio import fakes, media_files  # noqa: F401  (fixtures)


def W(words, t0=0.0, step=0.3):
    return [{"word": w, "start": t0 + i * step, "end": t0 + i * step + 0.25} for i, w in enumerate(words)]


def test_group_words_no_dangling_connectors():
    g = group_words(W(["contexto", "amigos", "el", "problema", "es", "grave."]), 3)
    texts = [" ".join(w["word"] for w in x) for x in g]
    assert all(not t.split()[-1] in ("el",) for t in texts), texts
    assert any(t.startswith("el problema") for t in texts), texts
    g2 = group_words(W(["hola,", "amigos", "del", "canal"]), 6)
    assert [len(x) for x in g2] == [1, 3] or [len(x) for x in g2] == [4]


def test_infer_and_long_informative_style():
    p = {"format": "horizontal", "source_meta": {"duration": 468}, "style": {}}
    b = brief.infer(p)
    assert b["video_type"] == "long" and b["edit_style"] == "informativo"
    st, pace = brief.style_for(p, b)
    assert st["caption_style"] == "clean" and st["caption_uppercase"] is False
    assert st["sfx_density"] <= 0.1 and st["transition_style"] == "cortes" and st["music_volume"] <= 0.06
    assert pace["captions_on"] is False and pace["broll_every_s"] == 45
    st2, pace2 = brief.style_for(p, {**b, "captions": "on", "music": "off"})
    assert pace2["captions_on"] and st2["music_volume"] == 0


def test_learned_style_respected_but_capped_for_long(client, monkeypatch):
    learned = {"caption_color": "#FF00AA", "transition_style": "dinamico", "sfx_density": 0.5, "sfx_per_min": 12,
               "zoom_amount": 1.2, "zoom_every": 1, "caption_style": "karaoke", "sound_roles": "impacto"}
    monkeypatch.setattr(brief, "learned_style", lambda sid: learned)
    p = {"format": "horizontal", "source_meta": {"duration": 600}, "style": {}}
    b = {**brief.infer(p), "learned_style_id": 3}
    st, _ = brief.style_for(p, b)
    assert st["caption_color"] == "#FF00AA" and st["sound_roles"] == "impacto"  # el look del estilo aprendido
    assert st["sfx_per_min"] <= 2.0 and st["zoom_amount"] <= 1.11 and st["transition_style"] == "suave"
    ps = {"format": "vertical", "source_meta": {"duration": 40}, "style": {}}
    st_s, _ = brief.style_for(ps, {**brief.infer(ps), "learned_style_id": 3})
    assert st_s["sfx_per_min"] == 12 and st_s["transition_style"] == "dinamico"  # en shorts, tal cual


def test_normalize_attachments():
    b = brief.normalize({"attachments": [{"video_id": "4", "role": "chroma_bg"}, {"video_id": 4}, {"video_id": "x"}],
                         "video_type": "raro", "captions": "on"})
    assert b["attachments"] == [{"video_id": 4, "role": "chroma_bg", "note": ""}]
    assert b["video_type"] is None and b["captions"] == "on"


def test_spread_broll_from_library(client):
    with db.get_conn() as conn:
        vid = conn.execute("INSERT INTO videos (title, original_path, stored_path, storage_mode, duration) "
                           "VALUES ('gameplay', 'g', 'g#1', 'reference', 1000)").lastrowid
    segs = [{"start": t, "src_in": t, "src_out": t + 10} for t in range(0, 300, 10)]
    tl = {"duration": 300, "tracks": {"video": segs, "broll": [], "text": [], "music": [], "sfx": []}}
    n = agent.spread_broll(tl, [{"video_id": vid, "duration": 1000, "role": "broll"}], 45)
    assert 4 <= n <= 7
    starts = [b["start"] for b in tl["tracks"]["broll"]]
    assert all(b2 - b1 >= 45 for b1, b2 in zip(starts, starts[1:]))
    assert len({b["src_in"] for b in tl["tracks"]["broll"]}) == n  # tramos distintos


def test_clean_captions_ass():
    tl = {"width": 1920, "height": 1080, "words": W(["Hola", "amigos", "del", "canal."]), "captions": {"enabled": True},
          "tracks": {"text": []}, "layout": {}}
    st = {"caption_size": 60, "caption_color": "#FFFFFF", "highlight_color": "#FFE500", "caption_font": "Arial Black",
          "title_font": "Arial Black", "title_color": "#FFFFFF", "title_bg": "#7C5CFF", "callout_color": "#FFE500",
          "caption_style": "clean", "caption_uppercase": True, "caption_max_words": 7, "caption_position": "bottom"}
    ass = render.build_ass(tl, st)
    assert "Style: CapClean" in ass and ",CapClean,," in ass and "HOLA" not in ass and "Hola" in ass


def test_autoedit_with_brief(client, fakes, media_files):
    from test_studio import _upload
    lib = make_video_in_lib(client, media_files)
    up = _upload(client, media_files["video"])
    b = {"video_type": "long", "edit_style": "informativo", "captions": "off", "chroma": "off",
         "attachments": [{"video_id": lib, "role": "broll"}]}
    res = client.post("/api/studio/projects", json={"name": "Largo", "source_type": "upload", "upload_id": up,
                                                    "format": "horizontal", "brief": b}).json()
    pid = res["project"]["id"]
    job = client.get(f"/api/jobs/{res['job']['id']}").json()
    assert job["status"] == "done", job
    assert job["result"]["ai"]["brief"]["video_type"] == "long"
    p = client.get(f"/api/studio/projects/{pid}").json()
    assert p["timeline"]["captions"]["enabled"] is False
    assert p["style"]["caption_style"] == "clean" and p["style"]["transition_style"] == "cortes"
    got = client.get(f"/api/studio/projects/{pid}/brief").json()
    assert got["saved"]["attachments"][0]["video_id"] == lib
    # re-auto-editar con otro brief (short + subtítulos)
    j2 = client.post(f"/api/studio/projects/{pid}/autoedit", json={"brief": {**b, "video_type": "short",
                                                                             "captions": "on"}}).json()
    assert client.get(f"/api/jobs/{j2['id']}").json()["status"] == "done"
    p2 = client.get(f"/api/studio/projects/{pid}").json()
    assert p2["timeline"]["captions"]["enabled"] is True and p2["style"]["caption_style"] == "karaoke"


def make_video_in_lib(client, media_files):
    r = client.post("/api/library/import", json={"paths": [str(media_files["video"])]}).json()
    x = r["results"][0]
    return x["video"]["id"] if x.get("video") else x["video_id"]


def test_callout_must_be_said():
    tl = {"words": W(["Eneba", "no", "entrega", "los", "códigos", "de", "GTA", "seis"], t0=10)}
    assert agent.callout_is_said(tl, "No entrega los códigos", 11)
    assert not agent.callout_is_said(tl, "GTA no llegó a Xbox", 11)
    assert not agent.callout_is_said(tl, "No entrega los códigos", 60)  # dicho en otro momento


def test_long_serious_no_memes_and_spaced_fx(monkeypatch):
    from app.studio import director
    monkeypatch.setattr(brief, "learned_style", lambda sid: {"transition_style": "dinamico", "sfx_density": 0.5})
    p = {"format": "horizontal", "source_meta": {"duration": 455}, "style": {}}
    st, _ = brief.style_for(p, {**brief.infer(p), "learned_style_id": 6})
    assert st["memes"] is False and st["fx_gap_s"] == 15.0
    tl = {"tracks": {"video": [{"start": 0, "fx": [{"t": 1}, {"t": 5}, {"t": 20}]}, {"start": 30, "fx": [{"t": 2}, {"t": 9}]}]}}
    assert director.thin_fx(tl, 15) == 2
    assert [f["t"] for f in tl["tracks"]["video"][0]["fx"]] == [1, 20] and [f["t"] for f in tl["tracks"]["video"][1]["fx"]] == [9]
