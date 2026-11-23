"""Clip → proyecto editable del Studio, encuadres y motor único de render."""
import pytest

from conftest import make_video, needs_ffmpeg
from app.studio import director
from app.studio import timeline as tlm
from app.studio.render import caption_margin, layout_of


def _vid(client, dirs, name="edit.mp4", seconds=24):
    v = make_video(dirs["videos"] / name, w=1280, h=720, seconds=seconds)
    r = client.post("/api/library/import", json={"paths": [str(v)]}).json()
    return r["results"][0]["video"]["id"] if r["imported"] else r["results"][0]["video_id"]


@needs_ffmpeg
@pytest.mark.parametrize("mode,layout", [("blur", "blur"), ("center", "crop"), ("fit", "fit")])
def test_clip_to_project_and_render(client, dirs, mode, layout):
    vid = _vid(client, dirs, f"edit_{mode}.mp4")
    c = client.post("/api/clips", json={"video_id": vid, "start_s": 2, "end_s": 12, "title": "Prueba"}).json()
    j = client.post(f"/api/clips/{c['id']}/edit", json={"crop_mode": mode, "show_title": True, "title": "HOLA",
                                                        "viral": True}).json()
    j = client.get(f"/api/jobs/{j['id']}").json()
    assert j["status"] == "done", j
    pid = j["result"]["project_id"]
    p = client.get(f"/api/studio/projects/{pid}").json()
    tl = p["timeline"]
    assert tl["layout"]["mode"] == layout and abs(tl["duration"] - 10) < 0.05
    assert tl["tracks"]["video"][0]["src_in"] == 2 and tl["tracks"]["text"][0]["text"] == "HOLA"
    rj = client.post(f"/api/studio/projects/{pid}/render", json={"quality": "preview"}).json()
    assert client.get(f"/api/jobs/{rj['id']}").json()["status"] == "done"
    client.delete(f"/api/studio/projects/{pid}")
    client.delete(f"/api/library/{vid}")


@needs_ffmpeg
def test_split_layout_renders_and_survives_save(client, dirs):
    vid = _vid(client, dirs, "edit_split.mp4", seconds=8)
    res = client.post("/api/studio/projects", json={"name": "split", "source_type": "library", "video_id": vid,
                                                    "format": "vertical", "auto_edit": False}).json()
    pid = res["project"]["id"]
    tl = res["project"]["timeline"]
    tl["layout"] = {"mode": "split", "cam": {"x": 0.02, "y": 0.05, "w": 0.25, "h": 0.3}, "game_x": 0.6}
    tl["tracks"]["video"][0]["fx"] = [{"t": 2.0, "type": "punch", "amount": 0.11, "hold": 1.0}]
    saved = client.put(f"/api/studio/projects/{pid}/timeline", json={"timeline": tl, "note": "split"}).json()
    assert saved["timeline"]["layout"]["mode"] == "split"
    assert saved["timeline"]["tracks"]["video"][0]["fx"][0]["hold"] == 1.0
    rj = client.post(f"/api/studio/projects/{pid}/render", json={"quality": "preview"}).json()
    assert client.get(f"/api/jobs/{rj['id']}").json()["status"] == "done"
    client.delete(f"/api/studio/projects/{pid}")
    client.delete(f"/api/library/{vid}")


def test_layout_defaults_and_caption_position():
    assert layout_of({})["mode"] == "crop"
    assert layout_of({"layout": {"mode": "split"}})["mode"] == "blur"  # sin cámara no hay stream
    st = {"caption_position": "auto"}
    tl = {"width": 1080, "height": 1920, "layout": {"mode": "split", "cam": {"x": 0, "y": 0, "w": 0.2, "h": 0.2}}}
    m_split = caption_margin(tl, st, 88)
    m_crop = caption_margin({**tl, "layout": {"mode": "crop"}}, st, 88)
    m_blur = caption_margin({**tl, "layout": {"mode": "blur", "src_ar": 16 / 9}}, st, 88)
    assert m_split > m_blur > m_crop * 0.5  # en stream bajo la cámara (más arriba), en desenfocado bajo la imagen
    assert caption_margin(tl, {"caption_position": "bottom"}, 88) == int(1920 * 0.27)


def test_director_spacing_holds_and_no_ui_sounds(monkeypatch):
    tl = tlm.empty("vertical")
    tl["tracks"]["video"] = [{"id": "v", "src_in": 0, "src_out": 30, "zoom": 1, "crop_x": 0.5}]
    words = [{"start": i * 0.5, "end": i * 0.5 + 0.4, "word": "hola." if i % 6 == 5 else "hola"} for i in range(58)]
    tlm.relayout(tl, words, 30)
    fake = [{"id": 1, "title": "click 001", "tags": "interface sounds click", "duration": 0.1, "available": True},
            {"id": 2, "title": "Impacto", "tags": "impacto boom golpe", "duration": 0.9, "available": True},
            {"id": 3, "title": "Pop", "tags": "pop texto", "duration": 0.2, "available": True}]
    monkeypatch.setattr(director.assets, "list_assets", lambda kind=None: fake)
    monkeypatch.setattr(director.llm, "status", lambda url: {"available": True, "models": ["m"]})
    monkeypatch.setattr(director.db, "role_model", lambda role: "m")
    monkeypatch.setattr(director.llm, "chat_json", lambda *a, **k: {"events": [
        {"t": 2.0, "type": "punch"}, {"t": 3.0, "type": "punch"},  # el segundo está demasiado cerca
        {"t": 10.0, "type": "sfx", "sound": "impacto"}, {"t": 11.0, "type": "sfx", "sound": "impacto"},
        {"t": 20.0, "type": "shake"}]})
    stats = director.direct(tl, {"callouts_per_min": 4, "fx_density": 1.0}, [(i * 0.5, 0.5) for i in range(60)],
                            None, "", None, window=90.0)
    fx = tl["tracks"]["video"][0]["fx"]
    punches = [f for f in fx if f["type"] == "punch"]
    assert len(punches) == 1 and 0.35 <= punches[0]["hold"] <= 2.2
    assert stats["shake"] == 0  # tramo < 40 s: sin sacudidas
    assert [x["asset_id"] for x in tl["tracks"]["sfx"]] == [2]  # un solo impacto (separación 4 s), nunca el clic
