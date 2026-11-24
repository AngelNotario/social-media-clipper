"""Zooms reales de la referencia: detección, estilo aprendido y colocación en momentos de énfasis."""
import subprocess

import numpy as np
import pytest

cv2 = pytest.importorskip("cv2")

from app.studio import learn, reference_rules, style as style_mod, zoom_detect  # noqa: E402


def _video(path, zfun, dur=12, fps=30, size=(640, 360), overlay=False):
    rng = np.random.default_rng(7)
    tex = cv2.GaussianBlur((rng.random((1400, 2400, 3)) * 255).astype(np.uint8), (0, 0), 2.5)
    W, H = size
    p = subprocess.Popen(["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{W}x{H}",
                          "-r", str(fps), "-i", "-", "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
                          str(path)], stdin=subprocess.PIPE)
    for i in range(dur * fps):
        t = i / fps
        z = zfun(t)
        cx, cy = 1000 + t * 12, 700 + t * 4
        w, h = 1280 / z, 720 / z
        crop = tex[int(cy - h / 2):int(cy + h / 2), int(cx - w / 2):int(cx + w / 2)]
        frame = cv2.resize(crop, (W, H), interpolation=cv2.INTER_AREA)
        if overlay:  # facecam y HUD fijos encima (como un stream de gameplay)
            frame[60:200, 0:230] = tex[100:240, 100:330]
            frame[10:40, W - 160:W - 10] = tex[300:330, 300:450]
        p.stdin.write(frame.tobytes())
    p.stdin.close()
    p.wait()
    return path


def test_detects_punches_and_hold(tmp_path):
    v = _video(tmp_path / "punch.mp4", lambda t: 1.15 if (3 <= t < 5 or 8 <= t < 10) else 1.0)
    z = zoom_detect.detect(v, 0, 12)
    ins = [e for e in z["events"] if e["dir"] == "in"]
    assert len(ins) == 2
    assert abs(ins[0]["t0"] - 3.0) < 0.2 and abs(ins[1]["t0"] - 8.0) < 0.2
    assert 1.1 <= z["amount"] <= 1.2
    assert 1.5 <= z["hold_s"] <= 2.5
    assert z["kind"] == "punch" and z["confidence"] >= 0.4


def test_game_camera_moving_forward_under_fixed_overlay_is_not_zoom(tmp_path):
    # la cámara del juego avanza (todo crece poco a poco) pero la facecam y el HUD no: no es un zoom de edición
    v = _video(tmp_path / "walk.mp4", lambda t: 1.0 + 0.1 * t, dur=6, overlay=True)
    z = zoom_detect.detect(v, 0, 8)
    assert [e for e in z["events"] if e["kind"] == "ramp"] == []


def test_game_camera_jump_under_fixed_overlay_is_not_punch(tmp_path):
    v = _video(tmp_path / "jump.mp4", lambda t: 1.12 if 2 <= t < 4 else 1.0, dur=6, overlay=True)
    assert zoom_detect.detect(v, 0, 6)["events"] == []


def test_pan_only_is_no_zoom(tmp_path):
    v = _video(tmp_path / "pan.mp4", lambda t: 1.0, dur=8)
    z = zoom_detect.detect(v, 0, 8)
    assert z["events"] == [] and z["zoom_per_min"] == 0


def test_zoom_fields_to_style():
    prof = {"zoom": {"confidence": 0.9, "zoom_per_min": 4.0, "amount": 1.15, "hold_s": 2.0, "kind": "punch"}}
    f = learn.zoom_fields(prof)
    assert f == {"zoom_per_min": 4.0, "zoom_kind": "punch", "zoom_amount": 1.15, "zoom_hold_s": 2.0}
    st = style_mod.clean_style(f)  # el estilo guardado conserva los campos nuevos
    assert st["zoom_kind"] == "punch" and st["zoom_per_min"] == 4.0
    none = learn.zoom_fields({"zoom": {"confidence": 1.0, "zoom_per_min": 0.0}})
    assert none["zoom_kind"] == "none" and none["zoom_amount"] == 1.0
    assert learn.zoom_fields({"zoom": {"confidence": 0.1, "zoom_per_min": 9}}) == {}


def test_edl_uses_measured_zooms():
    prof = {"duration": 30, "zoom": {"confidence": 0.9, "events": [
        {"t0": 3.0, "t1": 3.1, "kind": "punch", "dir": "in", "amount": 1.15, "hold_s": 2.0}]},
        "design_events": [{"t": 10.0, "zoomed_in": True}]}
    zooms = [e for e in learn.build_edl(prof) if e["type"] == "zoom"]
    assert len(zooms) == 1 and zooms[0]["t"] == 3.0 and zooms[0]["amount"] == 1.15


def _tl():
    words, t = [], 0.0
    text = ("hoy les traigo algo increíble . miren esto ¡ es brutal ! y lo mejor es que funciona . "
            "nadie lo esperaba ¿ verdad ? ahora vamos a probarlo en directo .").split()
    for w in text * 3:
        words.append({"start": round(t, 2), "end": round(t + 0.3, 2), "word": w})
        t += 0.4
    segs = [{"start": 0.0, "src_in": 0.0, "src_out": t / 2, "fx": [], "zoom": 1.0},
            {"start": t / 2, "src_in": 100.0, "src_out": 100.0 + t / 2, "fx": [], "zoom": 1.0}]
    return {"duration": t, "words": words, "tracks": {"video": segs, "text": []}}


def test_place_zooms_on_emphasis_with_reference_amount():
    tl = _tl()
    st = {"zoom_kind": "punch", "zoom_per_min": 6.0, "zoom_amount": 1.15, "zoom_hold_s": 1.8}
    n = reference_rules.place_zooms(tl, st)
    assert n >= 2
    fx = [(s["start"] + f["t"], f) for s in tl["tracks"]["video"] for f in s["fx"]]
    assert all(abs(f["amount"] - 0.15) < 1e-6 for _, f in fx)
    times = sorted(t for t, _ in fx)
    assert all(b - a >= 2.5 for a, b in zip(times, times[1:]))  # nunca seguidos
    # caen en palabras de énfasis (¡/!/¿/?)
    emph = {round(w["start"], 2) for w in tl["words"] if any(c in w["word"] for c in "¡!¿?")}
    assert sum(1 for t, _ in fx if any(abs(t + 0.05 - e) < 0.06 for e in emph)) >= 1
    assert n <= round(6.0 * tl["duration"] / 60)


def test_place_zooms_respects_no_zoom_reference():
    tl = _tl()
    assert reference_rules.place_zooms(tl, {"zoom_kind": "none", "zoom_per_min": 0}) == 0
    assert reference_rules.place_zooms(tl, {}) == 0  # estilo sin medir: no cambia nada


def test_measured_style_disables_alternation():
    from app.studio import autoedit
    assert autoedit.measured_zoom({"zoom_kind": "punch"})
    assert not autoedit.measured_zoom({"zoom_kind": ""})


def test_toggle_false_positive_zoom(client):
    import json
    from app import db
    learn.ensure_schema()
    ev = [{"t0": 5.0, "t1": 5.0, "kind": "punch", "dir": "in", "amount": 1.15, "hold_s": 2.0},
          {"t0": 20.0, "t1": 20.0, "kind": "punch", "dir": "in", "amount": 1.9, "hold_s": 1.0}]
    prof = {"duration": 60, "analyzed_seconds": 60,
            "zoom": {"events": ev, "confidence": 0.9, "analyzed_s": 60, "zoom_per_min": 2.0, "amount": 1.525}}
    with db.get_conn() as c:
        sid = c.execute("INSERT INTO learned_styles (name, source, profile, style) VALUES (?,?,?,?)",
                        ("z", "z", json.dumps(prof), json.dumps({}))).lastrowid
    r = client.post(f"/api/studio/styles/{sid}/zooms/toggle", json={"video": 0, "index": 1, "enabled": False})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["zoom"]["zoom_per_min"] == 1.0 and d["zoom"]["amount"] == 1.15
    assert d["style"]["zoom_amount"] == 1.15
    zooms = [e for e in learn.style_profile(sid)["edl"] if e["type"] == "zoom"]
    assert [e["t"] for e in zooms] == [5.0]
    assert client.post(f"/api/studio/styles/{sid}/zooms/toggle", json={"index": 9, "enabled": True}).status_code == 400
