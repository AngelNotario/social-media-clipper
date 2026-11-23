"""Juez de estilo: vocabulario visual de la referencia, nota de similitud, consejos y ajustes aprendidos."""
import json

from app import db
from app.studio import learn, style_judge as sj


def _tl(n_broll=6, mode="card", dur=120.0, sched=None):
    broll = [{"id": f"b{i}", "asset_id": 1, "start": 10 + i * 15, "end": 14 + i * 15, "mode": mode} for i in range(n_broll)]
    segs = [{"id": "a", "start": 0, "src_in": 0, "src_out": 60, "zoom": 1.0, "fx": [{"t": 5, "type": "punch"}]},
            {"id": "b", "start": 60, "src_in": 60, "src_out": 120, "zoom": 1.1, "transition": "cut"}]
    return {"duration": dur, "captions": {"enabled": False},
            "chroma": {"enabled": bool(sched), "bg_schedule": sched or []},
            "tracks": {"video": segs, "broll": broll, "sfx": [{"id": "s", "asset_id": 1, "start": 10}],
                       "text": [{"start": 1, "end": 3, "kind": "title", "text": "T"},
                                {"start": 30, "end": 33, "kind": "lower", "text": "Dato"}],
                       "music": [{"asset_id": 2, "start": 0, "end": 120, "volume": 0.06}]}}


def test_edit_metrics_counts_resources_and_modes():
    m = sj.edit_metrics(_tl())
    assert m["overlays_per_min"] == 3.0 and m["card_ratio"] == 1.0 and m["fullscreen_ratio"] == 0.0
    assert m["text_per_min"] == 1.0 and m["zoom_per_min"] == 1.0  # un punch + un cambio de zoom en 2 min
    assert m["music"] == "tenue" and m["captions_ratio"] == 0.0 and m["sfx_per_min"] == 0.5
    assert m["overlay_ratio"] == 0.2


def test_bg_schedule_counts_as_fullscreen_changes_and_text():
    sched = [{"start": i * 4, "end": i * 4 + 4, "asset_id": 1} for i in range(25)]
    sched[3]["kind"] = "text"
    m = sj.edit_metrics(_tl(n_broll=0, sched=sched))
    assert m["fullscreen_ratio"] == 1.0 and m["overlay_ratio"] > 0.8
    assert m["visual_changes_per_min"] >= 12.5 and m["speaker_corner"] is True
    assert m["text_per_min"] == 1.5  # título + rótulo + tarjeta de texto del fondo
    ref = {"visual_changes_per_min": 14, "speaker_visible_ratio": 0.0, "fullscreen_ratio": 1.0}
    r = sj.similarity(ref, m)
    assert r["score"] >= 80, r


def test_similarity_identical_is_100_and_far_is_low():
    m = sj.edit_metrics(_tl())
    assert sj.similarity(dict(m), m)["score"] == 100
    far = sj.edit_metrics(_tl(n_broll=0))
    r = sj.similarity(sj.preset_targets("long", "informativo"), far)
    assert r["score"] < 60
    top = r["advice"][0]
    assert top["action"] == "more" and top["key"] in ("visual_changes_per_min", "overlays_per_min", "card_ratio", "overlay_ratio")


def test_advice_says_less_when_too_much():
    t = {"overlays_per_min": 1.0, "music": "no"}
    r = sj.similarity(t, {"overlays_per_min": 9.0, "music": "sí"})
    acts = {a["key"]: a["action"] for a in r["advice"]}
    assert acts == {"overlays_per_min": "less", "music": "less"}
    assert all(a["why_es"] for a in r["advice"])


def test_summarize_elements_and_to_style_mapping():
    obs = []
    for i in range(30):
        ov = "card" if i % 3 == 0 else ("fullscreen_broll" if i % 3 == 1 else "none")
        obs.append({"overlay": ov, "overlay_count": 1 if ov != "none" else 0, "speaker_visible": ov != "fullscreen_broll",
                    "speaker_layout": "corner", "background": "chroma_replaced", "text_on_screen": i % 5 == 0,
                    "text_kind": "label" if i % 5 == 0 else "none", "animation_hint": "zoom" if i % 10 == 0 else "none"})
    el = learn.summarize_elements(obs, 240.0)
    assert el["overlay_ratio"] == 0.67 and el["card_ratio"] == 0.5 and el["fullscreen_ratio"] == 0.5
    assert el["overlays_per_min"] == 5.0 and el["speaker_layout"] == "corner" and el["memes_ratio"] == 0.0
    assert el["labels_per_min"] == 1.5 and el["sampled"] == 30
    prof = {"edit": {"cuts_per_10s": 1.0}, "elements": el}
    es = learn.elements_style(prof)
    assert es["broll_per_min"] == 5.0 and es["card_style"] in ("card", "full") and es["labels"] and es["memes"] is False
    st = learn.to_style(prof)
    assert st["memes"] is False and st["callouts_per_min"] == 2
    agg = learn.combine([prof | {"duration": 300}, prof | {"duration": 200}])
    assert agg["elements"]["overlays_per_min"] == 5.0 and agg["elements"]["sampled"] == 60


def test_element_times_bounded():
    t = learn.element_times(600)
    assert len(t) <= learn.ELEMENT_MAX_FRAMES and t[0] > 0 and t[-1] < 600
    assert len(learn.element_times(60)) == 7


def test_reference_targets_and_targets_for(client):
    learn.ensure_schema()
    prof = {"analyzed_seconds": 180, "edit": {"cuts_per_10s": 2.0, "cuts_per_min": 15.0},
            "sound": {"music_bed": "tenue"}, "sound_pro": {"sfx_per_min": 4.0},
            "design": {"captions_ratio": 0.0},
            "elements": {"overlays_per_min": 12.0, "card_ratio": 0.3, "fullscreen_ratio": 0.7, "labels_per_min": 2.0,
                         "zoom_per_min": 1.0, "memes_ratio": 0.0, "speaker_visible_ratio": 0.05, "overlay_ratio": 0.9}}
    with db.get_conn() as conn:
        sid = conn.execute("INSERT INTO learned_styles (name, source, profile, style) VALUES ('Ref', 'x', ?, '{}')",
                           (json.dumps(prof),)).lastrowid
    t = sj.reference_targets(sid)
    assert t["overlays_per_min"] == 12.0 and t["cuts_per_min"] == 15.0 and t["visual_changes_per_min"] == 15.0
    assert t["sfx_per_min"] == 4.0 and t["music"] == "tenue" and t["pip_ratio"] is None
    full = sj.targets_for(sid, "long", "informativo")
    assert full["pip_ratio"] == 0.05 and full["sources"]["pip_ratio"] == "preset"
    assert full["sources"]["overlays_per_min"] == "referencia" and full["name"] == "Ref"
    assert sj.reference_targets(999999) is None and sj.reference_targets(None) is None


def test_tuning_keeps_best(client):
    sj.save_tuning(7, "informativo", {"broll_per_min": 3}, 70)
    sj.save_tuning(7, "informativo", {"broll_per_min": 5}, 85)
    r = sj.save_tuning(7, "informativo", {"broll_per_min": 9}, 60)
    assert r["params"] == {"broll_per_min": 5} and r["score"] == 85 and r["runs"] == 3
    assert sj.load_tuning(7, "tutorial") is None


def test_visual_compare_mocked(monkeypatch, tmp_path):
    from PIL import Image
    from app.ai import llm
    frames = []
    for i in range(4):
        p = tmp_path / f"f{i}.jpg"
        Image.new("RGB", (64, 36), (i * 50, 0, 0)).save(p)
        frames.append(p)
    monkeypatch.setattr(llm, "status", lambda url: {"available": True, "models": [db.get_setting("vision_model") or "qwen3.6:35b-a3b"]})
    seen = {}

    def fake(url, model, system, user, schema, images=None, **kw):
        seen["n"] = len(images or [])
        return {"score": 72, "notes": "Faltan tarjetas"}
    monkeypatch.setattr(llm, "chat_json", fake)
    r = sj.visual_compare(frames[:2], frames[2:])
    assert r == {"score": 72.0, "notes_es": "Faltan tarjetas"} and seen["n"] == 1
    monkeypatch.setattr(llm, "status", lambda url: {"available": False, "models": []})
    assert sj.visual_compare(frames[:2], frames[2:]) is None
