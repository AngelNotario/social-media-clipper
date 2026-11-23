"""Edición generativa: colocación por rondas y autoevaluación (sin red ni modelos)."""
from app.studio import generative, style_judge


def _tl(dur=120.0):
    segs, t = [], 0.0
    while t < dur:
        segs.append({"id": f"v{int(t)}", "start": t, "src_in": t, "src_out": t + 10, "transition": "cut", "fx": []})
        t += 10
    return {"width": 1920, "height": 1080, "duration": dur, "words": [], "captions": {"enabled": False},
            "chroma": {"enabled": True, "protect": [{"x": 0.01, "y": 0.1, "w": 0.2, "h": 0.28}]},
            "tracks": {"video": segs, "broll": [], "text": [], "music": [], "sfx": []}}


def _items(dur=120.0, step=4.0):
    out, t, k = [], 0.0, 0
    while t < dur - 3:
        if k % 5 == 4:
            res = {"kind": "text", "text": "MARKETPLACE", "bg_color": "#E8424F"}
        else:
            res = {"kind": "media", "asset_id": 100 + k, "media_type": "image", "fit": "contain", "reused": k % 2 == 0}
        out.append({"need": {"t0": t, "t1": t + 4, "entity": "Eneba", "what": "logo", "label": "Eneba no es oficial" if k % 7 == 0 else "",
                             "priority": 2}, "resource": res})
        t += step
        k += 1
    return out


def test_generative_reaches_goal_with_background(client, monkeypatch):
    from app import resources
    monkeypatch.setattr(resources, "gather", lambda *a, **k: _items())
    monkeypatch.setattr(style_judge, "targets_for", lambda sid, vt, es: {
        "name": "ref", "visual_changes_per_min": 12, "overlays_per_min": 12, "fullscreen_ratio": 1.0,
        "text_per_min": 1.0, "zoom_per_min": 0.5, "music": "no", "speaker_visible_ratio": 0.0})
    tl = _tl()
    info = generative.run(tl, {"style_id": None, "video_type": "long", "edit_style": "informativo",
                               "chroma_protected": True})
    sched = tl["chroma"]["bg_schedule"]
    assert len(sched) >= 10 and all(b["end"] > b["start"] for b in sched)
    assert all(sched[i]["start"] >= sched[i - 1]["end"] - 1e-6 for i in range(1, len(sched)))  # sin encimarse
    assert any(e.get("kind") == "text" for e in sched)
    assert info["rounds"] and info["score"] >= 0 and info["music_ref"] == "no"
    again = style_judge.load_tuning(None, "informativo")
    assert again and again["params"]["visual_per_min"] > 0  # se guardó para la próxima


def test_generative_cards_without_chroma(client, monkeypatch):
    from app import resources
    monkeypatch.setattr(resources, "gather", lambda *a, **k: _items())
    tl = _tl()
    tl["chroma"] = {"enabled": False}
    generative.run(tl, {"video_type": "long", "edit_style": "entretenimiento", "chroma_protected": False,
                        "face_side": "left"})
    cards = [b for b in tl["tracks"]["broll"] if b.get("mode") == "card"]
    assert cards and all(b["pos"]["x"] > 0.5 for b in cards)  # del lado contrario a la cara


def test_cards_vertical_clip_go_above_the_face(client, monkeypatch):
    """En clips verticales las tarjetas van arriba (no tapan la cara ni los subtítulos) y duran poco."""
    from app import resources
    monkeypatch.setattr(resources, "gather", lambda *a, **k: _items(45.0, 3.0))
    tl = _tl(45.0)
    tl["width"], tl["height"], tl["chroma"] = 1080, 1920, {}
    before = len(tl["tracks"]["text"])
    generative.run(tl, {"video_type": "short", "edit_style": "viral", "card_zone": "top", "labels": False, "zoom": False})
    cards = [b for b in tl["tracks"]["broll"] if b.get("mode") == "card"]
    assert cards and all(b["pos"]["y"] < 0.3 and b["end"] - b["start"] <= 3.5 for b in cards)
    assert len(tl["tracks"]["text"]) == before  # sin rótulos extra que choquen con subtítulos
    assert not any(f.get("gen") for s in tl["tracks"]["video"] for f in s.get("fx") or [])  # zooms: los del director
    game = _tl(45.0)
    game["width"], game["height"], game["chroma"] = 1080, 1920, {}
    generative.run(game, {"video_type": "short", "edit_style": "gaming", "card_zone": "game", "labels": False, "zoom": False})
    assert all(b["pos"]["y"] > 0.6 for b in game["tracks"]["broll"] if b.get("mode") == "card")


def test_clip_project_runs_generative(client, monkeypatch):
    """El editor de clips (render directo y «Editar en Studio») llama a la edición generativa con zona correcta."""
    from app import resources
    from app.studio import agent, clip_project, style
    monkeypatch.setattr(resources, "gather", lambda *a, **k: _items(30.0, 3.0))
    monkeypatch.setattr(agent, "llm_ready", lambda: True)
    tl = _tl(30.0)
    tl["width"], tl["height"], tl["chroma"] = 1080, 1920, {}
    tl["words"] = [{"word": "Eneba", "start": 1.0, "end": 1.4}]
    c = {"title": "Eneba no entrega", "reason": "polémica", "hook": "¿Te pasó?"}
    info = clip_project._generative(tl, style.clean_style({}), c, {}, {"mode": "split", "cam": {"x": 0, "y": 0, "w": .3, "h": .3}},
                                    lambda p, s: None)
    cards = [b for b in tl["tracks"]["broll"] if b.get("mode") == "card"]
    assert info and info.get("found") and cards and all(b["pos"]["y"] > 0.6 for b in cards)  # sobre el juego
    tl2 = _tl(30.0)
    tl2["words"] = []
    assert clip_project._generative(tl2, style.clean_style({}), c, {}, {"mode": "crop"}, lambda p, s: None)["skipped"]
