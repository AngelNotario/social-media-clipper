"""add_background (la operación que agregó otra IA): el juego no debe tapar la cámara."""
from app import db
from app.studio import agent, style
from conftest import make_video


def _tl(w, h):
    return {"width": w, "height": h, "duration": 30.0, "fps": 30, "words": [], "captions": {"enabled": False},
            "tracks": {"video": [{"id": "v1", "start": 0.0, "src_in": 0.0, "src_out": 30.0, "transition": "cut"}],
                       "broll": [], "text": [], "music": [], "sfx": []}}


def _vid(client, tmp_path):
    v = make_video(tmp_path / "gameplay.mp4", seconds=3)
    r = client.post("/api/library/import", json={"paths": [str(v)]}).json()
    res = r["results"][0]
    return res["video"]["id"] if res.get("video") else res["video_id"]  # ya importado por la otra prueba


def test_split_vertical_is_a_strip_not_fullscreen(client, dirs, tmp_path):
    vid = _vid(client, dirs["videos"])
    tl = _tl(1080, 1920)
    agent.apply_ops(tl, style.clean_style({}), [{"op": "add_background", "video_id": vid, "start": 0, "end": 20, "mode": "split"}])
    b = tl["tracks"]["broll"][0]
    assert b["mode"] == "pip" and b["pos"]["y"] > 0.6 and b["frame"] == "none"  # franja abajo, no pantalla completa
    assert b["volume"] == 0 and "layout" not in tl  # la toma principal no se recorta como si fuera un stream
    client.delete(f"/api/library/{vid}")  # no dejar videos que cuenten en otras pruebas


def test_split_horizontal_goes_right(client, dirs, tmp_path):
    vid = _vid(client, dirs["videos"])
    tl = _tl(1920, 1080)
    agent.apply_ops(tl, style.clean_style({}), [{"op": "add_background", "video_id": vid, "start": 0, "end": 20, "mode": "split"}])
    b = tl["tracks"]["broll"][0]
    assert b["mode"] == "pip" and b["pos"]["x"] > 0.6 and b["scale"] == 0.5
    client.delete(f"/api/library/{vid}")
